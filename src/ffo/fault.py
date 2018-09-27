from beat.utility import list2string
from beat.fast_sweeping import fast_sweep

from .laplacian import get_smoothing_operator
from .base import backends

from pyrocko.gf.seismosizer import Cloneable

import copy
from logging import getLogger
from collections import namedtuple

import numpy as num

from theano import shared


logger = getLogger('ffo.fault')


__all__ = [
    'FaultGeometry',
    'FaultOrdering',
    'discretize_sources']


slip_directions = {
    'uparr': {'slip': 1., 'rake': 0.},
    'uperp': {'slip': 1., 'rake': -90.},
    'utensile': {'slip': 0., 'rake': 0., 'opening': 1.}}


PatchMap = namedtuple(
    'PatchMap', 'count, slc, shp, npatches, indexmap')


km = 1000.


class FaultGeometry(Cloneable):
    """
    Object to construct complex fault-geometries with several subfaults.
    Stores information for subfault geometries and
    inversion variables (e.g. slip-components).
    Yields patch objects for requested subfault, dataset and component.

    Parameters
    ----------
    datatypes : list
        of str of potential dataset fault geometries to be stored
    components : list
        of str of potential inversion variables (e.g. slip-components) to
        be stored
    ordering : :class:`FaultOrdering`
        comprises patch information related to subfaults
    """

    def __init__(self, datatypes, components, ordering):
        self.datatypes = datatypes
        self.components = components
        self._ext_sources = {}
        self.ordering = ordering

    def __str__(self):
        s = '''
Complex Fault Geometry
number of subfaults: %i
number of patches: %i ''' % (
            self.nsubfaults, self.npatches)
        return s

    def _check_datatype(self, datatype):
        if datatype not in self.datatypes:
            raise TypeError(
                'Datatype "%s" not included in FaultGeometry' % datatype)

    def _check_component(self, component):
        if component not in self.components:
            raise TypeError('Component not included in FaultGeometry')

    def _check_index(self, index):
        if index > self.nsubfaults - 1:
            raise TypeError('Subfault with index %i not defined!' % index)

    def get_subfault_key(self, index, datatype, component):

        if datatype is not None:
            self._check_datatype(datatype)
        else:
            datatype = self.datatypes[0]

        if component is not None:
            self._check_component(component)
        else:
            component = self.components[0]

        self._check_index(index)

        return datatype + '_' + component + '_' + str(index)

    def setup_subfaults(self, datatype, component, ext_sources, replace=False):

        self._check_datatype(datatype)
        self._check_component(component)

        if len(ext_sources) != self.nsubfaults:
            raise FaultGeometryError('Setup does not match fault ordering!')

        for i, source in enumerate(ext_sources):
            source_key = self.get_subfault_key(i, datatype, component)

            if source_key not in self._ext_sources.keys() or replace:
                self._ext_sources[source_key] = copy.deepcopy(source)
            else:
                raise FaultGeometryError(
                    'Subfault already specified in geometry!')

    def _assign_datatype(self, datatype):
        if datatype is None:
            return self.datatypes[0]
        else:
            return datatype

    def _assign_component(self, component):
        if component is None:
            return self.components[0]
        else:
            return component

    def iter_subfaults(self, datatype=None, component=None):
        """
        Iterator over subfaults.
        """
        datatype = self._assign_datatype(datatype)
        component = self._assign_component(component)

        for i in range(self.nsubfaults):
            yield self.get_subfault(
                index=i, datatype=datatype, component=component)

    def get_subfault(self, index, datatype=None, component=None):

        datatype = self._assign_datatype(datatype)
        component = self._assign_component(component)

        source_key = self.get_subfault_key(index, datatype, component)

        if source_key in self._ext_sources.keys():
            return self._ext_sources[source_key]
        else:
            raise FaultGeometryError('Requested subfault not defined!')

    def get_subfault_patches(self, index, datatype=None, component=None):
        """
        Get all Patches to a subfault in the geometry.

        Parameters
        ----------
        index : int
            to subfault
        datatype : str
            to return 'seismic' or 'geodetic'
        """
        self._check_index(index)

        datatype = self._assign_datatype(datatype)
        component = self._assign_component(component)

        subfault = self.get_subfault(
            index, datatype=datatype, component=component)
        npw, npl = self.get_subfault_discretization(index)

        return subfault.patches(nl=npl, nw=npw, datatype=datatype)

    def get_all_patches(self, datatype=None, component=None):
        """
        Get all RectangularSource patches for the full complex fault.

        Parameters
        ----------
        datatype : str
            'geodetic' or 'seismic'
        component : str
            slip component to return may be %s
        """ % list2string(slip_directions.keys())

        datatype = self._assign_datatype(datatype)
        component = self._assign_component(component)

        patches = []
        for i in range(self.nsubfaults):
            patches += self.get_subfault_patches(
                i, datatype=datatype, component=component)

        return patches

    def get_patch_indexes(self, index):
        """
        Return indexes for sub-fault patches that translate to the solution
        array.

        Parameters
        ----------
        index : int
            to the sub-fault

        Returns
        -------
        slice : slice
            to the solution array that is being extracted from the related
            :class:`pymc3.backends.base.MultiTrace`
        """
        self._check_index(index)
        return self.ordering.vmap[index].slc

    def get_subfault_discretization(self, index):
        """
        Return number of patches in strike and dip-directions of a subfault.

        Parameters
        ----------
        index : int
            index to the subfault

        Returns
        -------
        tuple (dip, strike)
            number of patches in fault direction
        """
        self._check_index(index)
        return self.ordering.vmap[index].shp

    def point2starttimes(self, point, index=0):
        """
        Calculate starttimes for point in solution space.
        """

        nuc_dip = point['nucleation_dip']
        nuc_strike = point['nucleation_strike']
        velocities = point['velocities']

        nuc_dip_idx, nuc_strike_idx = self.fault_locations2idxs(
            nuc_dip, nuc_strike, backend='numpy')
        return self.get_subfault_starttimes(
            index, velocities, nuc_dip_idx, nuc_strike_idx)

    def get_subfault_starttimes(
            self, index, rupture_velocities, nuc_dip_idx, nuc_strike_idx):
        """
        Get maximum bound of start times of extending rupture along
        the sub-fault.

        Parameters
        ----------
        index : int
            index to the subfault
        rupture_velocities : :class:`numpy.NdArray`
            of rupture velocities for each patch, (N x 1) for N patches [km/s]
        nuc_dip_idx : int
            rupture nucleation idx to patch in dip-direction
        nuc_strike_idx : int
            rupture nucleation idx to patch in strike-direction
        """

        npw, npl = self.get_subfault_discretization(index)
        slownesses = 1. / rupture_velocities.reshape((npw, npl))

        start_times = fast_sweep.get_rupture_times_numpy(
            slownesses, self.ordering.patch_size_dip,
            n_patch_strike=npl, n_patch_dip=npw,
            nuc_x=nuc_strike_idx, nuc_y=nuc_dip_idx)
        return start_times

    def get_subfault_smoothing_operator(self, index):
        """
        Get second order Laplacian smoothing operator.

        This is beeing used to smooth the slip-distribution
        in the optimization.


        Returns
        -------
        :class:`numpy.Ndarray`
            (n_patch_strike + n_patch_dip) x (n_patch_strike + n_patch_dip)
        """

        npw, npl = self.get_subfault_discretization(index)
        return get_smoothing_operator(
            n_patch_strike=npl,
            n_patch_dip=npw,
            patch_size_strike=self.ordering.patch_size_strike * km,
            patch_size_dip=self.ordering.patch_size_dip * km)

    def fault_locations2idxs(
            self, positions_dip, positions_strike, backend='numpy'):
        """
        Return patch indexes for given location on the fault.

        Parameters
        ----------
        positions_dip : :class:`numpy.NdArray` float
            of positions in dip direction of the fault [km]
        positions_strike : :class:`numpy.NdArray` float
            of positions in strike direction of the fault [km]
        backend : str
            which implementation backend to use [numpy/theano]
        """

        dipidx = positions2idxs(
            positions=positions_dip,
            cell_size=self.ordering.patch_size_dip,
            backend=backend)
        strikeidx = positions2idxs(
            positions=positions_strike,
            cell_size=self.ordering.patch_size_strike,
            backend=backend)
        return dipidx, strikeidx

    def patchmap(self, index, dipidx, strikeidx):
        """
        Return mapping of strike and dip indexes to patch index.
        """
        return self.ordering.vmap[index].indexmap[dipidx, strikeidx]

    def spatchmap(self, index, dipidx, strikeidx):
        """
        Return mapping of strike and dip indexes to patch index.
        """
        return self.ordering.smap[index][dipidx, strikeidx]

    @property
    def nsubfaults(self):
        return len(self.ordering.vmap)

    @property
    def npatches(self):
        return self.ordering.npatches


class FaultOrdering(object):
    """
    A mapping of source patches to the arrays of optimization results.

    Parameters
    ----------
    npls : list
        of number of patches in strike-direction
    npws : list
        of number of patches in dip-direction
    patch_size_strike : float
        patch size in strike-direction [km]
    patch_size_dip : float
        patch size in dip-direction [km]
    """

    def __init__(self, npls, npws, patch_size_strike, patch_size_dip):

        self.patch_size_dip = patch_size_dip
        self.patch_size_strike = patch_size_strike
        self.vmap = []
        self.smap = []
        dim = 0
        count = 0

        for npl, npw in zip(npls, npws):
            npatches = npl * npw
            slc = slice(dim, dim + npatches)
            shp = (npw, npl)
            indexes = num.arange(npatches, dtype='int16').reshape(shp)
            self.vmap.append(PatchMap(count, slc, shp, npatches, indexes))
            self.smap.append(shared(
                indexes,
                name='patchidx_array_%i' % count,
                borrow=True).astype('int16'))
            dim += npatches
            count += 1

        self.npatches = dim


class FaultGeometryError(Exception):
    pass


def positions2idxs(positions, cell_size, backend='numpy'):
    """
    Return index to a grid with a given cell size.npatches

    Parameters
    ----------
    positions : :class:`numpy.NdArray` float
        of positions [km]
    cell_size : float
        size of grid cells
    backend : str
    """
    available_backends = backends.keys()
    if backend not in available_backends:
        raise NotImplementedError(
            'Backend not supported! Options: %s' %
            list2string(available_backends))

    return backends[backend].round((positions - (
        cell_size / 2.)) / cell_size).astype('int16')


def discretize_sources(
        sources=None, extension_width=0.1, extension_length=0.1,
        patch_width=5., patch_length=5., datatypes=['geodetic'],
        varnames=['']):
    """
    Build complex discretized fault.

    Extend sources into all directions and discretize sources into patches.
    Rounds dimensions to have no half-patches.

    Parameters
    ----------
    sources : :class:`sources.RectangularSource`
        Reference plane, which is being extended and
    extension_width : float
        factor to extend source in width (dip-direction)
    extension_length : float
        factor extend source in length (strike-direction)
    patch_width : float
        Width [km] of subpatch in dip-direction
    patch_length : float
        Length [km] of subpatch in strike-direction
    varnames : list
        of str with variable names that are being optimized for

    Returns
    -------
    :class:'FaultGeometry'
    """
    if 'seismic' in datatypes and patch_length != patch_width:
        raise ValueError(
            'Seismic kinematic fault optimization does only support'
            ' square patches (yet)! Please adjust the discretization!')

    nsources = len(sources)
    if 'seismic' in datatypes and nsources > 1:
        raise ValueError(
            'Seismic kinematic fault optimization does'
            ' only support one main fault (TODO fast'
            ' sweeping across sub-faults)!'
            ' nsources defined: %i' % nsources)

    patch_length_m = patch_length * km
    patch_width_m = patch_width * km

    npls = []
    npws = []
    for source in sources:
        s = copy.deepcopy(source)
        ext_source = s.extent_source(
            extension_width, extension_length,
            patch_width_m, patch_length_m)

        npls.append(int(num.ceil(ext_source.length / patch_length_m)))
        npws.append(int(num.ceil(ext_source.width / patch_width_m)))

    ordering = FaultOrdering(
        npls, npws, patch_size_strike=patch_length, patch_size_dip=patch_width)

    fault = FaultGeometry(datatypes, varnames, ordering)

    for datatype in datatypes:
        logger.info('Discretizing %s source(s)' % datatype)

        for var in varnames:
            logger.info('%s slip component' % var)
            param_mod = copy.deepcopy(slip_directions[var])

            ext_sources = []
            for source in sources:
                s = copy.deepcopy(source)
                param_mod['rake'] += s.rake
                s.update(**param_mod)

                ext_source = s.extent_source(
                    extension_width, extension_length,
                    patch_width_m, patch_length_m)

                npls.append(
                    ext_source.get_n_patches(patch_length_m, 'length'))
                npws.append(
                    ext_source.get_n_patches(patch_width_m, 'width'))
                ext_sources.append(ext_source)
                logger.info('Extended fault(s): \n %s' % ext_source.__str__())

            fault.setup_subfaults(datatype, var, ext_sources)

    return fault
