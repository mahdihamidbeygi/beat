import unittest
from beat.heart import (radiation_weights, radiation_matmul)
from beat.utility import get_random_uniform
from pyrocko.plot import beachball

from pyrocko.moment_tensor import symmat6, MomentTensor
from pyrocko import util

from copy import deepcopy
import numpy as num
from numpy.testing import assert_allclose

import os
import logging
from time import time


logger = logging.getLogger('test_heart')
km = 1000.


num.random.seed(45)


class TestPolarity(unittest.TestCase):

    def setUp(self):
        """
        Taken from Dahm, T.: Relative moment tensor inversion based on ray
        theory: theory and synthetic tests, 1996, GJI
        """
        self.azimuths_deg = [
            10., 35., 85., 55., 64., 40., 110., 125., 140., 160., -155., -70.]
        self.takeoff_angles_deg = [
            20., 45., 23., 39., 25., 32., 16., 41., 10., 41., 70., 90]

        self.azimuths_rad = num.deg2rad(self.azimuths_deg)
        self.takeoff_angles_rad = num.deg2rad(self.takeoff_angles_deg)

        self.m6 = [-0.87, 0.85, 0.02, 0.48, 0.04, -0.17]
        self.m6arr = num.array(self.m6)
        self.m9 = symmat6(*self.m6)

    def test_radiation(self):

        wavenames = ['any_P', 'any_SH', 'any_SV']

        mt = MomentTensor.from_values(self.m6)
        print(mt)

        for wavename in wavenames:

            t0 = time()
            amps = radiation_matmul(
                self.m9, self.takeoff_angles_rad, self.azimuths_rad,
                wavename=wavename)
            t1 = time()

            t2 = time()
            rad_weights = radiation_weights(
                self.takeoff_angles_rad, self.azimuths_rad,
                wavename=wavename)
            amps_weights = rad_weights.T.dot(self.m6arr)
            t3 = time()

            t_matmul = t1 - t0
            t_weights = t3 - t2

            print(wavename)
            print('matrix mul', amps)
            print('weights', amps_weights)
            print('times:\n matmul: %f weights: %f rel_diff: %f' % (
                t_matmul, t_weights, t_matmul / t_weights))
            assert_allclose(amps, amps_weights, atol=1e-6, rtol=1e-6)

    def test_polarity_bb(self):

        from matplotlib import pyplot as plt
        from beat.plotting import draw_ray_piercing_points_bb

        nstations = 1000
        takeoff_angles_rad = num.deg2rad(
            get_random_uniform(lower=1, upper=90, dimension=nstations))
        azimuths_rad = num.deg2rad(
            get_random_uniform(lower=-180., upper=180, dimension=nstations))

        kwargs = {
            'beachball_type': 'full',
            'size': 6,
            'size_units': 'data',
            'position': (4, 4),
            'color_t': 'black',
            'edgecolor': 'black',
            'grid_resolution': 400}

        #fig = plt.figure(figsize=)
        fig, axs = plt.subplots(1, 3, figsize=(15., 5.))
        fig.subplots_adjust(left=0., right=1., bottom=0., top=1.)

        wavenames = ['any_P', 'any_SH', 'any_SV']
        for i, wavename in enumerate(wavenames):
            ax = axs[i]
            rad_weights = radiation_weights(
                takeoff_angles_rad, azimuths_rad, wavename=wavename)
            amps_weights = rad_weights.T.dot(self.m6arr)

            transform, position, size = beachball.choose_transform(
                ax, kwargs['size_units'], kwargs['position'], kwargs['size'])
            beachball.plot_fuzzy_beachball_mpl_pixmap(
                num.atleast_2d(self.m6arr), ax, best_mt=None, **kwargs)
            draw_ray_piercing_points_bb(
                ax, takeoff_angles_rad, azimuths_rad, amps_weights,
                size=size, position=position, transform=transform)
            ax.set_title(wavename, fontsize=12)

        plt.show()


if __name__ == "__main__":
    util.setup_logging('test_heart', 'info')
    unittest.main()
