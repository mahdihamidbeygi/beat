import logging
import os
import copy

import numpy as num

from matplotlib import pyplot as plt
from matplotlib.ticker import MaxNLocator

from pymc3 import quantiles
from pymc3 import plots as pmp

from beat import utility
from beat.models import Stage, load_stage
from beat.heart import physical_bounds
from beat.config import geometry_mode_str, dist_vars

from .common import plot_units, get_result_point

from scipy.stats import kde

from pyrocko.plot import AutoScaler, nice_value
from pyrocko.cake_plot import str_to_mpl_color as scolor
from pyrocko.plot import mpl_graph_color, mpl_papersize


logger = logging.getLogger('plotting.marginals')


def unify_tick_intervals(axs, varnames, ntickmarks_max=5, axis='x'):
    """
    Take figure axes objects and determine unit ranges between common
    unit classes (see utility.grouped_vars). Assures that the number of
    increments is not larger than ntickmarks_max. Will thus overwrite

    Returns
    -------
    dict : with types_sets keys and (min_range, max_range) as values
    """
    unities = {}
    for setname in utility.unit_sets.keys():
        unities[setname] = [num.inf, -num.inf]

    def extract_type_range(ax, varname, unities):
        for setname, ranges in unities.items():
            if axis == 'x':
                varrange = num.diff(ax.get_xlim())
            elif axis == 'y':
                varrange = num.diff(ax.get_ylim())
            else:
                raise ValueError('Only "x" or "y" allowed!')

            tset = utility.unit_sets[setname]
            min_range, max_range = ranges
            if varname in tset:
                new_ranges = copy.deepcopy(ranges)
                if varrange < min_range:
                    new_ranges[0] = varrange
                if varrange > max_range:
                    new_ranges[1] = varrange

                unities[setname] = new_ranges

    for ax, varname in zip(axs.ravel('F'), varnames):
        extract_type_range(ax, varname, unities)

    for setname, ranges in unities.items():
        min_range, max_range = ranges
        max_range_frac = max_range / ntickmarks_max
        if max_range_frac > min_range:
            logger.debug(
                'Range difference between min and max for %s is large!'
                ' Extending min_range to %f' % (setname, max_range_frac))
            unities[setname] = [max_range_frac, max_range]

    return unities


def apply_unified_axis(axs, varnames, unities, axis='x', ntickmarks_max=3,
                       scale_factor=2 / 3):
    for ax, v in zip(axs.ravel('F'), varnames):
        if v in utility.grouped_vars:
            for setname, varrange in unities.items():
                if v in utility.unit_sets[setname]:
                    inc = nice_value(varrange[0] * scale_factor)
                    autos = AutoScaler(
                        inc=inc, snap='on', approx_ticks=ntickmarks_max)
                    if axis == 'x':
                        min, max = ax.get_xlim()
                    elif axis == 'y':
                        min, max = ax.get_ylim()

                    min, max, sinc = autos.make_scale(
                        (min, max), override_mode='min-max')

                    # check physical bounds if passed truncate
                    phys_min, phys_max = physical_bounds[v]
                    if min < phys_min:
                        min = phys_min
                    if max > phys_max:
                        max = phys_max

                    if axis == 'x':
                        ax.set_xlim((min, max))
                    elif axis == 'y':
                        ax.set_ylim((min, max))

                    ticks = num.arange(min, max + inc, inc).tolist()
                    if axis == 'x':
                        ax.xaxis.set_ticks(ticks)
                    elif axis == 'y':
                        ax.yaxis.set_ticks(ticks)
        else:
            ticker = MaxNLocator(nbins=3)
            if axis == 'x':
                ax.get_xaxis().set_major_locator(ticker)
            elif axis == 'y':
                ax.get_yaxis().set_major_locator(ticker)


def traceplot(
        trace, varnames=None, transform=lambda x: x, figsize=None,
        lines={}, chains=None, combined=False, grid=False,
        varbins=None, nbins=40, color=None, source_idxs=None,
        alpha=0.35, priors=None, prior_alpha=1, prior_style='--',
        axs=None, posterior=None, fig=None, plot_style='kde',
        prior_bounds={}, unify=True, qlist=[0.1, 99.9], kwargs={}):
    """
    Plots posterior pdfs as histograms from multiple mtrace objects.

    Modified from pymc3.

    Parameters
    ----------

    trace : result of MCMC run
    varnames : list of variable names
        Variables to be plotted, if None all variable are plotted
    transform : callable
        Function to transform data (defaults to identity)
    posterior : str
        To mark posterior value in distribution 'max', 'min', 'mean', 'all'
    figsize : figure size tuple
        If None, size is (12, num of variables * 2) inch
    lines : dict
        Dictionary of variable name / value  to be overplotted as vertical
        lines to the posteriors and horizontal lines on sample values
        e.g. mean of posteriors, true values of a simulation
    chains : int or list of ints
        chain indexes to select from the trace
    combined : bool
        Flag for combining multiple chains into a single chain. If False
        (default), chains will be plotted separately.
    source_idxs : list
        array like, indexes to sources to plot marginals
    grid : bool
        Flag for adding gridlines to histogram. Defaults to True.
    varbins : list of arrays
        List containing the binning arrays for the variables, if None they will
        be created.
    nbins : int
        Number of bins for each histogram
    color : tuple
        mpl color tuple
    alpha : float
        Alpha value for plot line. Defaults to 0.35.
    axs : axes
        Matplotlib axes. Defaults to None.
    fig : figure
        Matplotlib figure. Defaults to None.
    unify : bool
        If true axis units that belong to one group e.g. [km] will
        have common axis increments
    kwargs : dict
        for histplot op
    qlist : list
        of quantiles to plot. Default: (all, 0., 100.)

    Returns
    -------

    ax : matplotlib axes
    """
    ntickmarks = 2
    fontsize = 10
    ntickmarks_max = kwargs.pop('ntickmarks_max', 3)
    scale_factor = kwargs.pop('scale_factor', 2 / 3)
    lines_color = kwargs.pop('lines_color', 'red')

    num.set_printoptions(precision=3)

    def make_bins(data, nbins=40, qlist=None):
        d = data.flatten()
        if qlist is not None:
            qu = quantiles(d, qlist=qlist)
            mind = qu[qlist[0]]
            maxd = qu[qlist[-1]]
        else:
            mind = d.min()
            maxd = d.max()
        return num.linspace(mind, maxd, nbins)

    def remove_var(varnames, varname):
        idx = varnames.index(varname)
        varnames.pop(idx)

    if varnames is None:
        varnames = [name for name in trace.varnames if not name.endswith('_')]

    if 'geo_like' in varnames:
        remove_var(varnames, varname='geo_like')

    if 'seis_like' in varnames:
        remove_var(varnames, varname='seis_like')

    if posterior != 'None':
        llk = trace.get_values(
            'like', combine=combined, chains=chains, squeeze=False)
        llk = num.squeeze(transform(llk[0]))
        llk = pmp.utils.make_2d(llk)

        posterior_idxs = utility.get_fit_indexes(llk)

        colors = {
            'mean': scolor('orange1'),
            'min': scolor('butter1'),
            'max': scolor('scarletred2')}

    n = len(varnames)
    nrow = int(num.ceil(n / 2.))
    ncol = 2

    n_fig = nrow * ncol
    if figsize is None:
        if n < 5:
            figsize = mpl_papersize('a6', 'landscape')
        elif n < 7:
            figsize = mpl_papersize('a5', 'portrait')
        else:
            figsize = mpl_papersize('a4', 'portrait')

    if axs is None:
        fig, axs = plt.subplots(nrow, ncol, figsize=figsize)
        axs = num.atleast_2d(axs)
    elif axs.shape != (nrow, ncol):
        raise TypeError('traceplot requires n*2 subplots %i, %i' % (
                        nrow, ncol))

    if varbins is None:
        make_bins_flag = True
        varbins = []
    else:
        make_bins_flag = False

    input_color = copy.deepcopy(color)
    for i in range(n_fig):
        coli, rowi = utility.mod_i(i, nrow)

        if i > len(varnames) - 1:
            try:
                fig.delaxes(axs[rowi, coli])
            except KeyError:
                pass
        else:
            v = varnames[i]
            color = copy.deepcopy(input_color)

            for d in trace.get_values(
                    v, combine=combined, chains=chains, squeeze=False):
                d = transform(d)
                # iterate over columns in case varsize > 1

                if v in dist_vars:
                    if source_idxs is None:
                        logger.info('No patches defined using 1 every 10!')
                        source_idxs = num.arange(0, d.shape[1], 10).tolist()

                    logger.info(
                        'Plotting patches: %s' % utility.list2string(
                            source_idxs))

                    try:
                        selected = d.T[source_idxs]
                    except IndexError:
                        raise IndexError(
                            'One or several patches do not exist! '
                            'Patch idxs: %s' % utility.list2string(
                                source_idxs))
                else:
                    selected = d.T

                for isource, e in enumerate(selected):
                    e = pmp.utils.make_2d(e)
                    if make_bins_flag:
                        varbin = make_bins(e, nbins=nbins, qlist=qlist)
                        varbins.append(varbin)
                    else:
                        varbin = varbins[i]

                    if lines:
                        if v in lines:
                            reference = lines[v]
                        else:
                            reference = None
                    else:
                        reference = None

                    if color is None:
                        pcolor = mpl_graph_color(isource)
                    else:
                        pcolor = color

                    if plot_style == 'kde':
                        pmp.kdeplot(
                            e, shade=alpha, ax=axs[rowi, coli],
                            color=color, linewidth=1.,
                            kwargs_shade={'color': pcolor})
                        axs[rowi, coli].relim()
                        axs[rowi, coli].autoscale(tight=False)
                        axs[rowi, coli].set_ylim(0)
                        xax = axs[rowi, coli].get_xaxis()
                        # axs[rowi, coli].set_ylim([0, e.max()])
                        xticker = MaxNLocator(nbins=5)
                        xax.set_major_locator(xticker)
                    elif plot_style == 'hist':
                        histplot_op(
                            axs[rowi, coli], e, reference=reference,
                            bins=varbin, alpha=alpha, color=pcolor,
                            qlist=qlist, kwargs=kwargs)
                    else:
                        raise NotImplementedError(
                            'Plot style "%s" not implemented' % plot_style)

                    try:
                        param = prior_bounds[v]

                        if v in dist_vars:
                            try:  # variable bounds
                                lower = param.lower[source_idxs]
                                upper = param.upper[source_idxs]
                            except IndexError:
                                lower, upper = param.lower, param.upper

                            title = '{} {}'.format(v, plot_units[hypername(v)])
                        else:
                            lower = num.array2string(
                                param.lower, separator=',')[1:-1]
                            upper = num.array2string(
                                param.upper, separator=',')[1:-1]

                            title = '{} {} priors: ({}; {})'.format(
                                v, plot_units[hypername(v)], lower, upper)
                    except KeyError:
                        try:
                            title = '{} {}'.format(v, float(lines[v]))
                        except KeyError:
                            title = '{} {}'.format(v, plot_units[hypername(v)])

                    axs[rowi, coli].set_xlabel(title, fontsize=fontsize)
                    axs[rowi, coli].grid(grid)
                    axs[rowi, coli].get_yaxis().set_visible(False)
                    format_axes(axs[rowi, coli])
                    axs[rowi, coli].tick_params(axis='x', labelsize=fontsize)
    #                axs[rowi, coli].set_ylabel("Frequency")

                    if lines:
                        try:
                            axs[rowi, coli].axvline(
                                x=lines[v], color='white', lw=1.)
                            axs[rowi, coli].axvline(
                                x=lines[v], color='black', linestyle='dashed', lw=1.)
                        except KeyError:
                            pass

                    if posterior != 'None':
                        if posterior == 'all':
                            for k, idx in posterior_idxs.items():
                                axs[rowi, coli].axvline(
                                    x=e[idx], color=colors[k], lw=1.)
                        else:
                            idx = posterior_idxs[posterior]
                            axs[rowi, coli].axvline(
                                x=e[idx], color=pcolor, lw=1.)

    if unify:
        unities = unify_tick_intervals(
            axs, varnames, ntickmarks_max=ntickmarks_max, axis='x')
        apply_unified_axis(axs, varnames, unities, axis='x',
                           scale_factor=scale_factor)

    if source_idxs:
        axs[0, 0].legend(source_idxs)

    fig.tight_layout()
    return fig, axs, varbins


def correlation_plot(
        mtrace, varnames=None,
        transform=lambda x: x, figsize=None, cmap=None, grid=200, point=None,
        point_style='.', point_color='white', point_size='8'):
    """
    Plot 2d marginals (with kernel density estimation) showing the correlations
    of the model parameters.

    Parameters
    ----------
    mtrace : :class:`pymc3.base.MutliTrace`
        Mutlitrace instance containing the sampling results
    varnames : list of variable names
        Variables to be plotted, if None all variable are plotted
    transform : callable
        Function to transform data (defaults to identity)
    figsize : figure size tuple
        If None, size is (12, num of variables * 2) inch
    cmap : matplotlib colormap
    grid : resolution of kernel density estimation
    point : dict
        Dictionary of variable name / value  to be overplotted as marker
        to the posteriors e.g. mean of posteriors, true values of a simulation
    point_style : str
        style of marker according to matplotlib conventions
    point_color : str or tuple of 3
        color according to matplotlib convention
    point_size : str
        marker size according to matplotlib conventions

    Returns
    -------
    fig : figure object
    axs : subplot axis handles
    """

    if varnames is None:
        varnames = mtrace.varnames

    nvar = len(varnames)

    if figsize is None:
        figsize = mpl_papersize('a4', 'landscape')

    fig, axs = plt.subplots(
        sharey='row', sharex='col',
        nrows=nvar - 1, ncols=nvar - 1, figsize=figsize)

    d = dict()
    for var in varnames:
        vals = transform(
            mtrace.get_values(
                var, combine=True, squeeze=True))

        _, nvar_elements = vals.shape

        if nvar_elements > 1:
            raise ValueError(
                'Correlation plot can only be displayed for variables '
                ' with size 1! %s is %i! ' % (var, nvar_elements))

        d[var] = vals

    for k in range(nvar - 1):
        a = d[varnames[k]]
        for l in range(k + 1, nvar):
            logger.debug('%s, %s' % (varnames[k], varnames[l]))
            b = d[varnames[l]]

            kde2plot(
                a, b, grid=grid, ax=axs[l - 1, k], cmap=cmap, aspect='auto')

            if point is not None:
                axs[l - 1, k].plot(
                    point[varnames[k]], point[varnames[l]],
                    color=point_color, marker=point_style,
                    markersize=point_size)

            axs[l - 1, k].tick_params(direction='in')

            if k == 0:
                axs[l - 1, k].set_ylabel(varnames[l])

        axs[l - 1, k].set_xlabel(varnames[k])

    for k in range(nvar - 1):
        for l in range(k):
            fig.delaxes(axs[l, k])

    fig.tight_layout()
    fig.subplots_adjust(wspace=0.05, hspace=0.05)
    return fig, axs


def correlation_plot_hist(
        mtrace, varnames=None,
        transform=lambda x: x, figsize=None, hist_color='orange', cmap=None,
        grid=50, chains=None, ntickmarks=2, point=None,
        point_style='.', point_color='red', point_size=4, alpha=0.35,
        unify=True):
    """
    Plot 2d marginals (with kernel density estimation) showing the correlations
    of the model parameters. In the main diagonal is shown the parameter
    histograms.

    Parameters
    ----------
    mtrace : :class:`pymc3.base.MutliTrace`
        Mutlitrace instance containing the sampling results
    varnames : list of variable names
        Variables to be plotted, if None all variable are plotted
    transform : callable
        Function to transform data (defaults to identity)
    figsize : figure size tuple
        If None, size is (12, num of variables * 2) inch
    cmap : matplotlib colormap
    hist_color : str or tuple of 3
        color according to matplotlib convention
    grid : resolution of kernel density estimation
    chains : int or list of ints
        chain indexes to select from the trace
    ntickmarks : int
        number of ticks at the axis labels
    point : dict
        Dictionary of variable name / value  to be overplotted as marker
        to the posteriors e.g. mean of posteriors, true values of a simulation
    point_style : str
        style of marker according to matplotlib conventions
    point_color : str or tuple of 3
        color according to matplotlib convention
    point_size : str
        marker size according to matplotlib conventions
    unify: bool
        If true axis units that belong to one group e.g. [km] will
        have common axis increments

    Returns
    -------
    fig : figure object
    axs : subplot axis handles
    """
    fontsize = 9
    ntickmarks_max = 2
    label_pad = 25
    logger.info('Drawing correlation figure ...')

    if varnames is None:
        varnames = mtrace.varnames

    nvar = len(varnames)

    if figsize is None:
        if nvar < 5:
            figsize = mpl_papersize('a5', 'landscape')
        else:
            figsize = mpl_papersize('a4', 'landscape')

    fig, axs = plt.subplots(nrows=nvar, ncols=nvar, figsize=figsize)

    d = dict()

    for var in varnames:
        vals = transform(
            mtrace.get_values(
                var, chains=chains, combine=True, squeeze=True))

        _, nvar_elements = vals.shape

        if nvar_elements > 1:
            raise ValueError(
                'Correlation plot can only be displayed for variables '
                ' with size 1! %s is %i! ' % (var, nvar_elements))

        d[var] = vals

    hist_ylims = []
    for k in range(nvar):
        v_namea = varnames[k]
        a = d[v_namea]

        for l in range(k, nvar):
            v_nameb = varnames[l]
            logger.debug('%s, %s' % (v_namea, v_nameb))
            if l == k:
                if point is not None:
                    if v_namea in point.keys():
                        reference = point[v_namea]
                        axs[l, k].axvline(
                            x=reference, color=point_color,
                            lw=point_size / 4.)
                    else:
                        reference = None
                else:
                    reference = None

                histplot_op(
                    axs[l, k], pmp.utils.make_2d(a), alpha=alpha,
                    color='orange', tstd=0., reference=reference)

                axs[l, k].get_yaxis().set_visible(False)
                format_axes(axs[l, k])
                xticks = axs[l, k].get_xticks()
                xlim = axs[l, k].get_xlim()
                hist_ylims.append(axs[l, k].get_ylim())
            else:
                b = d[v_nameb]

                kde2plot(
                    a, b, grid=grid, ax=axs[l, k], cmap=cmap, aspect='auto')

                bmin = b.min()
                bmax = b.max()

                if point is not None:
                    if v_namea and v_nameb in point.keys():
                        axs[l, k].plot(
                            point[v_namea], point[v_nameb],
                            color=point_color, marker=point_style,
                            markersize=point_size)

                        bmin = num.minimum(bmin, point[v_nameb])
                        bmax = num.maximum(bmax, point[v_nameb])

                yticker = MaxNLocator(nbins=ntickmarks)
                axs[l, k].set_xticks(xticks)
                axs[l, k].set_xlim(xlim)
                yax = axs[l, k].get_yaxis()
                yax.set_major_locator(yticker)

            if l != nvar - 1:
                axs[l, k].get_xaxis().set_ticklabels([])

            if k == 0:
                axs[l, k].set_ylabel(
                    v_nameb + '\n ' + plot_units[hypername(v_nameb)],
                    fontsize=fontsize)
                if utility.is_odd(l):
                    axs[l, k].tick_params(axis='y', pad=label_pad)
            else:
                axs[l, k].get_yaxis().set_ticklabels([])

            axs[l, k].tick_params(
                axis='both', direction='in', labelsize=fontsize)

            try:    # matplotlib version issue workaround
                axs[l, k].tick_params(
                    axis='both', labelrotation=50.)
            except Exception:
                axs[l, k].set_xticklabels(
                    axs[l, k].get_xticklabels(), rotation=50)
                axs[l, k].set_yticklabels(
                    axs[l, k].get_yticklabels(), rotation=50)

            if utility.is_odd(k):
                axs[l, k].tick_params(axis='x', pad=label_pad)

        axs[l, k].set_xlabel(
            v_namea + '\n ' + plot_units[hypername(v_namea)],
            fontsize=fontsize)

    if unify:
        varnames_repeat_x = [
            var_reap for varname in varnames for var_reap in (varname,) * nvar]
        varnames_repeat_y = varnames * nvar
        unitiesx = unify_tick_intervals(
            axs, varnames_repeat_x, ntickmarks_max=ntickmarks_max, axis='x')
        apply_unified_axis(
            axs, varnames_repeat_x, unitiesx, axis='x', scale_factor=1.,
            ntickmarks_max=ntickmarks_max)
        apply_unified_axis(
            axs, varnames_repeat_y, unitiesx, axis='y', scale_factor=1.,
            ntickmarks_max=ntickmarks_max)

    for k in range(nvar):
        if unify:
            # reset histogram ylims after unify
            axs[k, k].set_ylim(hist_ylims[k])

        for l in range(k):
            fig.delaxes(axs[l, k])

    fig.tight_layout()
    fig.subplots_adjust(wspace=0.05, hspace=0.05)
    return fig, axs


def draw_posteriors(problem, plot_options):
    """
    Identify which stage is the last complete stage and plot posteriors.
    """

    hypers = utility.check_hyper_flag(problem)
    po = plot_options

    stage = Stage(homepath=problem.outfolder,
                  backend=problem.config.sampler_config.backend)

    pc = problem.config.problem_config

    list_indexes = stage.handler.get_stage_indexes(po.load_stage)

    if hypers:
        varnames = problem.hypernames + ['like']
    else:
        varnames = problem.varnames + problem.hypernames + \
            problem.hierarchicalnames + ['like']

    if len(po.varnames) > 0:
        varnames = po.varnames

    logger.info('Plotting variables: %s' % (', '.join((v for v in varnames))))
    figs = []

    for s in list_indexes:
        if po.source_idxs:
            sidxs = utility.list2string(po.source_idxs, fill='_')
        else:
            sidxs = ''

        outpath = os.path.join(
            problem.outfolder,
            po.figure_dir,
            'stage_%i_%s_%s.%s' % (s, sidxs, po.post_llk, po.outformat))

        if not os.path.exists(outpath) or po.force:
            logger.info('plotting stage: %s' % stage.handler.stage_path(s))
            stage.load_results(
                varnames=problem.varnames,
                model=problem.model, stage_number=s,
                load='trace', chains=[-1])

            prior_bounds = {}
            prior_bounds.update(**pc.hyperparameters)
            prior_bounds.update(**pc.hierarchicals)
            prior_bounds.update(**pc.priors)

            fig, _, _ = traceplot(
                stage.mtrace,
                varnames=varnames,
                chains=None,
                color='black',
                combined=True,
                source_idxs=po.source_idxs,
                plot_style='hist',
                lines=po.reference,
                posterior=po.post_llk,
                prior_bounds=prior_bounds)

            if not po.outformat == 'display':
                logger.info('saving figure to %s' % outpath)
                fig.savefig(outpath, format=po.outformat, dpi=po.dpi)
            else:
                figs.append(fig)

        else:
            logger.info(
                'plot for stage %s exists. Use force=True for'
                ' replotting!' % s)

    if po.outformat == 'display':
        plt.show()


def draw_correlation_hist(problem, plot_options):
    """
    Draw parameter correlation plot and histograms from the final atmip stage.
    Only feasible for 'geometry' problem.
    """

    if problem.config.problem_config.n_sources > 1:
        raise NotImplementedError(
            'correlation_hist plot not working (yet) for n_sources > 1')

    po = plot_options
    mode = problem.config.problem_config.mode

    assert mode == geometry_mode_str
    assert po.load_stage != 0

    hypers = utility.check_hyper_flag(problem)

    if hypers:
        varnames = problem.hypernames
    else:
        varnames = list(problem.varnames) + problem.hypernames + ['like']

    if len(po.varnames) > 0:
        varnames = po.varnames

    logger.info('Plotting variables: %s' % (', '.join((v for v in varnames))))

    if len(varnames) < 2:
        raise TypeError('Need at least two parameters to compare!'
                        'Found only %i variables! ' % len(varnames))

    stage = load_stage(
        problem, stage_number=po.load_stage, load='trace', chains=[-1])

    if not po.reference:
        reference = get_result_point(stage, problem.config, po.post_llk)
        llk_str = po.post_llk
    else:
        reference = po.reference
        llk_str = 'ref'

    outpath = os.path.join(
        problem.outfolder, po.figure_dir, 'corr_hist_%s_%s.%s' % (
            stage.number, llk_str, po.outformat))

    if not os.path.exists(outpath) or po.force:
        fig, axs = correlation_plot_hist(
            mtrace=stage.mtrace,
            varnames=varnames,
            cmap=plt.cm.gist_earth_r,
            chains=None,
            point=reference,
            point_size=6,
            point_color='red')
    else:
        logger.info('correlation plot exists. Use force=True for replotting!')
        return

    if po.outformat == 'display':
        plt.show()
    else:
        logger.info('saving figure to %s' % outpath)
        fig.savefig(outpath, format=po.outformat, dpi=po.dpi)
