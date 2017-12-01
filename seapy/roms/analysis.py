#!/usr/bin/env python
"""
  analysis.py

  Methods to assist in the analysis of ROMS fields

  Written by Brian Powell on 05/24/15
  Copyright (c)2017 University of Hawaii under the MIT-License.
"""

import numpy as np
from joblib import Parallel, delayed
import seapy
import netCDF4

# Create a function to do the multiprocessing vertical interpolation.
# This function is used because we don't want to construct an array of
# tuples from the oa routine.


def __dinterp(x, y, z, dat, fz, pmap):
    ndat, pm = seapy.oavol(x, y, z, dat, x, y, fz, pmap, 5, 1, 1)
    ndat = np.squeeze(ndat)
    return np.ma.masked_where(np.abs(ndat) > 9e10, ndat, copy=False)


def constant_depth(field, grid, depth, zeta=None, threads=-2):
    """
    Find the values of a 3-D field at a constant depth for all times given.

    Parameters
    ----------
    field : ndarray,
        ROMS 3-D field to interpolate onto a constant depth level. Can be
        two- or three-dimensional array (first dimension assumed to be time).
    grid : seapy.model.grid or string or list,
        Grid that defines the depths and stretching for the field given
    depth : float,
        Depth (in meters) to find all values
    zeta : ndarray, optional,
        ROMS zeta field corresponding to field if you wish to apply the SSH
        correction to the depth calculations.
    threads : int, optional,
        Number of threads to use for processing

    Returns
    -------
    nfield : ndarray,
        Values from ROMS field on the given constant depth
    """

    # Make sure our inputs are all valid
    grid = seapy.model.asgrid(grid)
    if np.ndim(field) == 3:
        field = seapy.adddim(field)
    if zeta is not None and np.ndim(zeta == 2):
        zeta = seapy.adddim(zeta)
    depth = depth if depth < 0 else -depth

    # Set up some arrays
    x, y = np.meshgrid(np.arange(field.shape[-1]), np.arange(field.shape[-2]))
    fz, pmap = seapy.oasurf(x, y, x, x, y, None, 5, 1, 1)
    fz = seapy.adddim(np.ones(x.shape)) * depth
    # Loop over all times, generate new field at depth
    nfield = np.ma.array(Parallel(n_jobs=threads, verbose=2)
                         (delayed(__dinterp)(x, y, grid.depth_rho,
                                             np.squeeze(field[i, :, :, :]), fz, pmap)
                          for i in range(field.shape[0])), copy=False)

    return nfield


def depth_average(field, grid, depth, top_depth=0, zeta=None):
    """
    Compute the depth-averaged field down to the depth specified. NOTE:
    This just finds the nearest layer, so at every grid cell, it may not be
    exactly the specified depth.

    Parameters
    ----------
    field : ndarray,
        ROMS 3-D field to integrate from a depth level. Must be
        three-dimensional array (single time).
    grid : seapy.model.grid or string or list,
        Grid that defines the depths and stretching for the field given
    depth : float,
        Depth (in meters) to integrate from
    top_depth : float,
        Depth (in meters) to integrate to
    zeta : ndarray, optional,
        ROMS zeta field corresponding to field if you wish to apply the SSH
        correction to the depth calculations.

    Returns
    -------
    ndarray,
        Values from depth integrated ROMS field
    """
    grid = seapy.model.asgrid(grid)
    depth = depth if depth < 0 else -depth
    top_depth = top_depth if top_depth < 0 else -top_depth
    if depth > top_depth:
        depth, top_depth = top_depth, depth
    drange = top_depth - depth

    # If we have zeta, we need to compute thickness
    if zeta is not None:
        s_w, cs_w = seapy.roms.stretching(grid.vstretching, grid.theta_s,
                                          grid.theta_b, grid.hc,
                                          grid.n, w_grid=True)
        depths = np.ma.masked_equal(seapy.roms.depth(
            grid.vtransform, grid.h, grid.hc, grid.s_rho, grid.cs_r) *
            grid.mask_rho, 0)

        thickness = np.ma.masked_array(seapy.roms.thickness(
            grid.vtransform, grid.h, grid.hc, s_w, cs_w, zeta) *
            grid.mask_rho, 0)

    else:
        depths = np.ma.masked_equal(grid.depth_rho * grid.mask_rho, 0)
        thickness = np.ma.masked_equal(grid.thick_rho * grid.mask_rho, 0)

    # If we are on u- or v-grid, transform
    if field.shape == grid.thick_u.shape:
        depths = seapy.model.rho2u(depths)
        thickness = seapy.model.rho2u(thickness)
    elif field.shape == grid.thick_v.shape:
        depths = seapy.model.rho2v(depths)
        thickness = seapy.model.rho2v(thickness)

    # 1. pick all of the points that are deeper and shallower than the limits
    k_ones = np.arange(grid.n, dtype=int)
    top_depth = depths[-1, :, :] if top_depth == 0 else top_depth
    upper = depths - top_depth
    upper[np.where(upper < 0)] = np.float('inf')
    lower = depths - depth
    lower[np.where(lower > 0)] = -np.float('inf')
    thickness *= np.ma.masked_equal(np.logical_and(
        k_ones[:, np.newaxis, np.newaxis] <= np.argmin(upper, axis=0),
        k_ones[:, np.newaxis, np.newaxis] >=
        np.argmax(lower, axis=0)).astype(int), 0)

    # Do the integration
    return np.sum(field * thickness, axis=0) / \
        np.sum(thickness, axis=0)


def transect(lon, lat, depth, data, nx=200, nz=40, z=None):
    """
    Generate an equidistant transect from data at varying depths. Can be
    used to plot a slice of model or observational data.

    Parameters
    ----------
    lat: array
        n-dimensional array with latitude of points
    lon: array
        n-dimensional array with longitude of points
    depth: array
        [k,n] dimensional array of the depths for all k-layers at each n point
    data: array
        [k,n] dimensional array of values
    nx: int, optional
        number of horizontal points desired in the transect
    nz: int, optional
        number of vertical points desired in the transect
    z: array, optional
        list of depths to use if you do not want equidistant depths

    Returns
    -------
    x: array
        x-location values of the new transect
    z: array
        z-location values of the new transect
    vals: array
        data values of the new transect
    """
    from scipy.interpolate import griddata
    depth = np.atleast_2d(depth)
    data = np.atleast_2d(data)
    lon = np.atleast_1d(lon)
    lat = np.atleast_1d(lat)

    # Generate the depths
    depth[depth > 0] *= -1
    if z is None:
        z = np.linspace(depth.min() - 2, depth.max(), nz)
    else:
        z[z > 0] *= -1
        nz = len(z)
    dz = np.abs(np.diff(z).mean())

    # Determine the distance between points and the weighting to apply
    dist = np.hstack(([0], seapy.earth_distance(
        lon[0], lat[0], lon[1:], lat[1:])))
    dx = np.diff(dist).mean()
    zscale = np.maximum(1, 10**int(np.log10(dx / dz)))
    dx /= zscale
    x = np.linspace(0, dist.max(), nx)

    # All arrays have to be the same size
    xx, zz = np.meshgrid(x / zscale, z)

    # For the source data, we don't want to extrpolate,
    # so make the data go from the surface to twice its
    # depth.
    zl = np.argsort(depth[:, 0])
    dep = np.vstack((np.ones((1, depth.shape[1])) * 2 * depth.min(),
                     depth[zl, :],
                     np.zeros((1, depth.shape[1]))))

    # repeat the same data at the top and bottom
    dat = np.vstack((data[zl[0], :], data[zl], data[zl[-1], :]))
    dist = np.tile(dist.T, [dep.shape[0], 1]) / zscale

    # Find the bottom indices to create a mask for nodata/land
    idx = np.interp(xx[0, :], dist[0, :],
                    np.interp(depth.min(axis=0), z,
                              np.arange(nz))).astype(int)
    mask = np.arange(nz)[:, np.newaxis] <= idx

    # Interpolate
    ndat = np.ma.array(griddata(
        (dist.ravel(), dep.ravel()), dat.ravel(), (xx.ravel(), zz.ravel()),
        method='cubic').reshape(xx.shape), mask=mask)

    # Return everything
    return x, z, ndat


def gen_std_i(roms_file, std_file, std_window=5, pad=1, skip=30, fields=None):
    """
    Create a std file for the given ocean fields. This std file can be used
    for initial conditions constraint in 4D-Var. This requires a long-term
    model spinup file from which to compute the standard deviation.

    Parameters
    ----------
    roms_file: string or list of strings,
        The ROMS (history or average) file from which to compute the std. If
        it is a list of strings, a netCDF4.MFDataset is opened instead.
    std_file: string,
        The name of the file to store the standard deviations fields
    std_window: int,
        The size of the window (in number of records) to compute the std over
    pad: int,
        How much to pad each side of the window for overlap. For example,
        std_window=10 and pad=2 would give a total window of 14 with 2 records
        used in the prior window and 2 in the post window as well.
    skip: int,
        How many records to skip at the beginning of the file
    fields: list of str,
        The fields to compute std for. Default is to use the ROMS prognostic
        variables.

    Returns
    -------
        None
    """
    # Create the fields to process
    if fields is None:
        fields = set(seapy.roms.fields)

    # Open the ROMS info
    grid = seapy.model.asgrid(roms_file)
    nc = seapy.netcdf(roms_file)

    # Filter the fields for the ones in the ROMS file
    fields = set(nc.variables).intersection(fields)

    # Build the output file
    time_var = seapy.roms.get_timevar(nc)
    epoch = netCDF4.num2date(0, nc.variables[time_var].units)
    time = nc.variables[time_var][:]
    ncout = seapy.roms.ncgen.create_da_ini_std(std_file,
                                               eta_rho=grid.ln, xi_rho=grid.lm, s_rho=grid.n,
                                               reftime=epoch, title="std from " + str(roms_file))
    grid.to_netcdf(ncout)

    # If there are any fields that are not in the standard output file,
    # add them to the output file
    for f in fields.difference(ncout.variables):
        ncout.createVariable(f, np.float32,
                             ('ocean_time', "s_rho", "eta_rho", "xi_rho"))

    # Loop over the time with the variance window:
    for n, t in enumerate(seapy.progressbar.progress(np.arange(skip + pad,
                                                               len(time) - std_window - pad, std_window))):
        idx = np.arange(t - pad, t + std_window + pad)
        ncout.variables[time_var][n] = np.mean(time[idx])
        for v in fields:
            dat = nc.variables[v][idx, :].std(axis=0)
            dat[dat > 10] = 0.0
            ncout.variables[v][n, :] = dat
        ncout.sync()
    ncout.close()
    nc.close()


def gen_std_f(roms_file, std_file, records=None, fields=None):
    """
    Create a std file for the given atmospheric forcing fields. This std
    file can be used for the forcing constraint in 4D-Var. This requires a
    long-term model spinup file from which to compute the standard deviation.

    Parameters
    ----------
    roms_file: string or list of strings,
        The ROMS (history or average) file from which to compute the std. If
        it is a list of strings, a netCDF4.MFDataset is opened instead.
    std_file: string,
        The name of the file to store the standard deviations fields
    records: ndarray,
        List of records to perform the std over. These records are used to
        avoid the solar diurnal cycles in the fields.
    fields: list of str,
        The fields to compute std for. Default is to use the ROMS atmospheric
        variables (sustr, svstr, shflux, ssflux).

    Returns
    -------
        None
    """
    # Create the fields to process
    if fields is None:
        fields = set(["sustr", "svstr", "shflux", "ssflux"])

    # Open the ROMS info
    grid = seapy.model.asgrid(roms_file)
    nc = seapy.netcdf(roms_file)

    # Filter the fields for the ones in the ROMS file
    fields = set(nc.variables).intersection(fields)

    # Build the output file
    time_var = seapy.roms.get_timevar(nc)
    epoch = netCDF4.num2date(0, nc.variables[time_var].units)
    time = nc.variables[time_var][:]
    ncout = seapy.roms.ncgen.create_da_frc_std(std_file,
                                               eta_rho=grid.ln, xi_rho=grid.lm, s_rho=grid.n,
                                               reftime=epoch, title="std from " + str(roms_file))
    grid.to_netcdf(ncout)

    # Set the records
    if records is None:
        records = np.arange(len(time))
    else:
        records = np.atleast_1d(records)
        records = records[records <= len(time)]

    # If there are any fields that are not part of the standard, add them
    # to the output file
    for f in fields.difference(ncout.variables):
        ncout.createVariable(f, np.float32,
                             ('ocean_time', "eta_rho", "xi_rho"))

    # Loop over the time with the variance window:
    ncout.variables[time_var][:] = np.mean(time[records])
    for v in fields:
        dat = nc.variables[v][records, :].std(axis=0)
        ncout.variables[v][0, :] = dat
        ncout.sync()
    ncout.close()
    nc.close()


def plot_obs_spatial(obs, type='zeta', prov=None, time=None, depth=0,
                     gridcoord=False, error=False, **kwargs):
    """
    Create a surface plot of the observations.

    Parameters
    ----------
    obs: filename, list, or observation class
        The observations to use for plotting
    type: string or int,
        The type of observation to plot ('zeta', 'temp', 'salt', etc.)
    prov: string or int,
        The provenance of the observations to plot
    time: ndarray,
        The times of the observations to plot
    depth: float,
        The depth of the obs to plot over the spatial region
    gridcoord: bool,
        If True, plot on grid coordinates. If False [default] plot on lat/lon
    error: bool,
        If True plot the errors rather than values. Default is False.
    **kwargs: keywords
        Passed to matplotlib.pyplot.scatter

    Returns
    -------
    None
    """
    import matplotlib.pyplot as plt

    obs = seapy.roms.obs.asobs(obs)
    otype = seapy.roms.obs.astype(type)
    if prov is not None:
        prov = seapy.roms.obs.asprovenance(prov)
    if time is not None:
        time = np.atleast_1d(time)

    # Search the obs for the user
    if prov is not None:
        idx = np.where(np.logical_and.reduce((
            obs.type == otype,
            obs.provenance == prov,
            np.logical_or(obs.z == 0, obs.depth == depth))))[0]

    else:
        idx = np.where(np.logical_and(
            obs.type == otype,
            np.logical_or(obs.z == 0, obs.depth == depth)))[0]

    # If there is a time specific condition, find the sets
    if time is not None:
        idx = idx[np.in1d(obs.time[idx], time)]

    # If we don't have anything to plot, return
    if not idx.any():
        return

    # Plot it up
    if not kwargs:
        kwargs = {'s': 30, 'alpha': 0.8, 'linewidths': (0, 0)}
    if gridcoord:
        x = obs.x
        y = obs.y
    else:
        x = obs.lon
        y = obs.lat
    val = obs.value if not error else np.sqrt(obs.error)
    plt.scatter(x[idx], y[idx], c=val[idx], **kwargs)
    plt.colorbar()


def plot_obs_profile(obs, type='temp', prov=None, time=None,
                     gridcoord=False, error=False, **kwargs):
    """
    Create a sub-surface profile plot of the observations.

    Parameters
    ----------
    obs: filename, list, or observation class
        The observations to use for plotting
    type: string or int,
        The type of observation to plot ('zeta', 'temp', 'salt', etc.)
    prov: string or int,
        The provenance of the observations to plot
    time: ndarray,
        The times of the observations to plot
    gridcoord: bool,
        If True, plot on grid coordinates. If False [default] plot on lat/lon
    error: bool,
        If True plot the errors rather than values. Default is False.
    **kwargs: keywords
        Passed to matplotlib.pyplot.scatter

    Returns
    -------
    None
    """
    import matplotlib.pyplot as plt

    obs = seapy.roms.obs.asobs(obs)
    otype = seapy.roms.obs.astype(type)
    if prov is not None:
        prov = seapy.roms.obs.asprovenance(prov)
    if time is not None:
        time = np.atleast_1d(time)

    # Search the obs for the user
    if prov is not None:
        idx = np.where(np.logical_and.reduce((
            obs.type == otype,
            obs.provenance == prov,
            np.logical_or(obs.z < 0, obs.depth < 0))))[0]

    else:
        idx = np.where(np.logical_and(
            obs.type == otype,
            np.logical_or(obs.z < 0, obs.depth < 0)))[0]

    # If there is a time specific condition, find the sets
    if time is not None:
        idx = idx[np.in1d(obs.time[idx], time)]

    # If we don't have anything to plot, return
    if not idx.any():
        return

    # Plot it up
    if gridcoord:
        dep = obs.z if np.mean(obs.z[idx] > 0) else obs.depth
    else:
        dep = obs.z if np.mean(obs.z[idx] < 0) else obs.depth
    val = obs.value if not error else np.sqrt(obs.error)
    plt.plot(val[idx], dep[idx], 'k+', **kwargs)

