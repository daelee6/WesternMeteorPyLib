#!/usr/bin/env python

from __future__ import print_function, division, absolute_import

import os
import sys
import time
import numpy as np
import numpy.ctypeslib as npct
import ctypes as ct

import scipy.optimize
import matplotlib.pyplot as plt
from matplotlib.pyplot import cm


from wmpl.Trajectory.Orbit import calcOrbit
from wmpl.Utils.TrajConversions import geo2Cartesian, raDec2ECI, altAz2RADec_vect, \
    raDec2AltAz_vect, jd2Date
from wmpl.Utils.Math import vectMag, findClosestPoints, sphericalToCartesian, lineFunc
from wmpl.Utils.OSTools import mkdirP
from wmpl.Utils.Pickling import savePickle

# Path to the compiled trajectory library
#TRAJ_LIBRARY = os.path.join('lib', 'libtrajectorysolution')
TRAJ_LIBRARY = os.path.join('lib', 'trajectory', 'libtrajectorysolution')

# Path to the default PSO configuration
PSO_CONFIG_PATH = os.path.join('lib', 'trajectory', 'conf', 'trajectorysolution.conf')


### VALUES FROM TrajectorySolution.h
# last "fit" mnemonic token value + 1
NFIT_TYPES = 4

# Unique ParameterRefinementViaPSO calls in trajectory
NPSO_CALLS = 6

######

# Init ctypes types
DOUBLE = ct.c_double
PDOUBLE = ct.POINTER(DOUBLE)
PPDOUBLE = ct.POINTER(PDOUBLE)
PPPDOUBLE = ct.POINTER(PPDOUBLE)


class PSO_info(ct.Structure):
    """ Mimicks PSO_info structure from the TrajectorySolution.h file, in Python-understandable
        ctypes format.
    """

    _fields_ = [
        ('number_particles', ct.c_int),
        ('maximum_iterations', ct.c_int),
        ('boundary_flag', ct.c_int),
        ('limits_flag', ct.c_int),
        ('particle_distribution_flag', ct.c_int),
        ('epsilon_convergence', ct.c_double),
        ('weight_inertia', ct.c_double),
        ('weight_stubborness', ct.c_double),
        ('weight_grouppressure', ct.c_double)
    ]



class TrajectoryInfo(ct.Structure):
    """ Mimicks the TrajectoryInfo structure from the TrajectorySolution.h file, in Python-understandable
        ctypes format.
    """

    _fields_ = [

        ### PARAMETERS REFLECTING INPUT SETTINGS and CONTROL

        # Max memory handling, intermediate reporting
        ('maxcameras', ct.c_int),
        ('verbose', ct.c_int),

        # Modeling parameters
        ('nummonte', ct.c_int),
        ('velmodel', ct.c_int),
        ('meastype', ct.c_int),

        # Reference timing and offset constraint
        ('jdt_ref', DOUBLE),
        ('max_toffset', DOUBLE),


        # PSO settings
        ('PSO_info', PSO_info*NFIT_TYPES),
        ('PSO_fit_control', ct.c_int*NPSO_CALLS),


        ### SOLUTION STARTUP AND DERIVED PRODUCTS

        # Memory allocation handling
        ('malloced', ct.POINTER(ct.c_int)),

        # Camera/site information
        ('numcameras', ct.c_int),
        ('camera_lat', PDOUBLE),
        ('camera_lon', PDOUBLE),
        ('camera_hkm', PDOUBLE),
        ('camera_LST', PDOUBLE),
        ('rcamera_ECI', PPPDOUBLE),

        # Measurement information
        ('nummeas', ct.POINTER(ct.c_int)),

        ('meas1', PPDOUBLE),
        ('meas2', PPDOUBLE),
        ('dtime', PPDOUBLE),
        ('noise', PPDOUBLE),
        ('weight', PPDOUBLE),

        ('meashat_ECI', PPPDOUBLE),
        ('ttbeg', DOUBLE),
        ('ttend', DOUBLE),
        ('ttzero', DOUBLE),

        # Trajectory fitting parameters to feed to the particle swarm optimizer
        ('numparams', ct.c_int),
        ('params', PDOUBLE),
        ('limits', PDOUBLE),
        ('xguess', PDOUBLE),
        ('xshift', PDOUBLE),


        ### SOLUTION OUTPUT PRODUCTS

        # Best solution vector of the parameter values of length numparams
        ('solution', PDOUBLE),

        # Primary output products and their standard deviations (sigma)
        ('ra_radiant', PDOUBLE),
        ('dec_radiant', PDOUBLE),

        ('vbegin', PDOUBLE),
        ('decel1', PDOUBLE),
        ('decel2', PDOUBLE),

        ('ra_sigma', PDOUBLE),
        ('dec_sigma', PDOUBLE),

        ('vbegin_sigma', PDOUBLE),
        ('decel1_sigma', PDOUBLE),
        ('decel2_sigma', PDOUBLE),

        # Intermediate bootstrapping solutions
        ('max_convergence', PDOUBLE),
        ('ra_radiant_IP', PDOUBLE),
        ('dec_radiant_IP', PDOUBLE),

        ('ra_radiant_IPW', PDOUBLE),
        ('dec_radiant_IPW', PDOUBLE),

        ('ra_radiant_LMS', PDOUBLE),
        ('dec_radiant_LMS', PDOUBLE),

        # Timing output relative to jdt_ref in seconds
        ('dtime_ref', PDOUBLE),
        ('dtime_tzero', PDOUBLE),
        ('dtime_beg', PDOUBLE),
        ('dtime_end', PDOUBLE),
        ('tref_offsets', PDOUBLE),

        # Measurement and model LLA, range and velocity arrays with dimension #cameras x #measurements(camera)
        ('meas_lat', PPDOUBLE),
        ('meas_lon', PPDOUBLE),
        ('meas_hkm', PPDOUBLE),
        ('meas_range', PPDOUBLE),
        ('meas_vel', PPDOUBLE),

        ('model_lat', PPDOUBLE),
        ('model_lon', PPDOUBLE),
        ('model_hkm', PPDOUBLE),
        ('model_range', PPDOUBLE),
        ('model_vel', PPDOUBLE),

        # Model fit vectors which follow the same "meastype" on output with dimension #cameras x #measurements(camera)
        ('model_fit1', PPDOUBLE),
        ('model_fit2', PPDOUBLE),
        ('model_time', PPDOUBLE),

        # BEGIN position and standard deviation in LLA
        ('rbeg_lat', PDOUBLE),
        ('rbeg_lon', PDOUBLE),
        ('rbeg_hkm', PDOUBLE),

        ('rbeg_lat_sigma', PDOUBLE),
        ('rbeg_lon_sigma', PDOUBLE),
        ('rbeg_hkm_sigma', PDOUBLE),

        # END position and standard deviation in LLA
        ('rend_lat', PDOUBLE),
        ('rend_lon', PDOUBLE),
        ('rend_hkm', PDOUBLE),

        ('rend_lat_sigma', PDOUBLE),
        ('rend_lon_sigma', PDOUBLE),
        ('rend_hkm_sigma', PDOUBLE)
    ]




def double2ArrayToPointer(arr):
    """ Converts a 2D numpy to ctypes 2D array.

    Arguments:
        arr: [ndarray] 2D numpy float64 array

    Return:
        arr_ptr: [ctypes double pointer]

    """

    # Init needed data types
    ARR_DIMX = DOUBLE*arr.shape[1]
    ARR_DIMY = PDOUBLE*arr.shape[0]

    # Init pointer
    arr_ptr = ARR_DIMY()

    # Fill the 2D ctypes array with values
    for i, row in enumerate(arr):
        arr_ptr[i] = ARR_DIMX()

        for j, val in enumerate(row):
            arr_ptr[i][j] = val


    return arr_ptr



def double1pointerToArray(ptr, n):
    """ Converts ctypes 1D array into a 1D numpy array.

    Arguments:
        ptr: [ctypes double pointer]
        n: [int] number of cameras

    Return:
        arr: [ndarrays] converted numpy array

    """

    # Init a new empty data array
    arr = np.zeros(shape=n)

    # Go through every camera
    for i in range(n):
        arr[i] = ptr[i]

    return arr



def double2pointerToArray(ptr, n, m_sizes):
    """ Converts ctypes 2D array into a 2D numpy array.

    Arguments:
        ptr: [ctypes double pointer]
        n: [int] number of cameras
        m_sizes: [list] number of measurements for each camera

    Return:
        arr_list: [list of ndarrays] list of numpy arrays, each list entry containing data for individual
            cameras

    """

    arr_list = []

    # Go through every camera
    for i in range(n):

        # Init a new empty data array
        arr = np.zeros(shape=(m_sizes[i]))

        # Go through ctypes array and extract data for this camera
        for j in range(m_sizes[i]):
            arr[j] = ptr[i][j]

        # Add the data for this camera to the final list
        arr_list.append(arr)

    return arr_list



def double3pointerToArray(ptr, n, m_sizes, p):
    """ Converts ctypes 3D array into a 3D numpy array.

    Arguments:
        ptr: [ctypes double pointer]
        n: [int] number of cameras
        m_sizes: [list] number of measurements for each camera
        p: [int] number of values for each measurement

    Return:
        arr_list: [list of ndarrays] list of numpy arrays, each list entry containing data for individual
            cameras

    """

    arr_list = []

    # Go through every camera
    for i in range(n):

        # Init a new empty data array
        arr = np.zeros(shape=(m_sizes[i], p))

        # Go through ctypes array and extract data for this camera
        for j in range(m_sizes[i]):

            # Go through every value for each measurement
            for k in range(p):
                arr[j,k] = ptr[i][j][k]

        # Add the data for this camera to the final list
        arr_list.append(arr)

    return arr_list



def fitLagIntercept(time, length, v_init, initial_intercept=0.0):
    """ Finds the intercept of the line with the given slope. Used for fitting time vs. length along the trail
        data.

    Arguments:
        time: [ndarray] Array containing the time data (seconds).
        length: [ndarray] Array containing the length along the trail data.
        v_init: [float] Fixed slope of the line (i.e. initial velocity).

    Keyword arguments:
        initial_intercept: [float] Initial estimate of the intercept.

    Return:
        (slope, intercept): [tuple of floats] fitted line parameters
    """

    # Fit a line to the first 25% of the points
    quart_size = int(0.25*len(time))

    # If the size is smaller than 4 points, take all point
    if quart_size < 4:
        quart_size = len(time)

    quart_length = length[:quart_size]
    quart_time = time[:quart_size]

    # Redo the lag fit, but with fixed velocity
    lag_intercept, _ = scipy.optimize.curve_fit(lambda x, intercept: lineFunc(x, v_init, intercept),
        quart_time, quart_length, p0=[initial_intercept])

    return v_init, lag_intercept[0]


class GuralTrajectory(object):
    """ Meteor trajectory estimation, using the Gural solver.

    IMPORTANT NOTE: If you are accessing measured/modeled data after running the solver, be sure to access it
    this way:

        traj = Trajectory(maxcameras, jdt_ref, ...)

        ...
        # Data input and solving...
        ...

        site_id = 0
        site_velocities = traj.meas_vel[site_id]

        # That way, you will select the data of the camera/site you want.

    """

    def __init__(self, maxcameras, jdt_ref, velmodel, max_toffset=1.0, nummonte=1, meastype=4, verbose=0,
        output_dir='.', pso_config=None, show_plots=True, save_results=True, traj_id=None,
        comment=''):
        """ Initialize meteor trajectory solving.

        Arguments:
            maxcameras: [int] Maximum number of cameras expected (to initially allocate arrays)
            jdt_ref: [float] Reference Julian date/time that the measurements times are provided relative to.
                This is user selectable and can be the time of the first camera, or the first measurement,
                or some average time for the meteor, but should be close to the time of passage of the meteor.
                This same reference date/time will be used on all camera measurements for the purposes of
                computing local sidereal time and making  geocentric coordinate transformations.
            velmodel: [int] Velocity propagation model
                0    = constant   v(t) = vinf
                0fha = constant, where the initial velocity is estimated as the average vel of the first half
                1    = linear     v(t) = vinf - |acc1| * t
                2    = quadratic  v(t) = vinf - |acc1| * t + acc2 * t^2
                3    = exponent   v(t) = vinf - |acc1| * |acc2| * exp( |acc2| * t )


        Keyword arguments:
            max_toffset: [float] Maximum allowed time offset between cameras in seconds
            nummonte: [float] Number of Monte Carlo trials for standard deviation calculation
            meastype: [float] Flag to indicate the type of angle measurements the user is providing
                for meas1 and meas2 below. The following are all in radians:
                    1 = Right Ascension for meas1, Declination for meas2.
                    2 = Azimuth +east of due north for meas1, Elevation angle
                        above the horizon for meas2
                    3 = Azimuth +west of due south for meas1, Zenith angle for meas2
                    4 = Azimuth +north of due east for meas1, Zenith angle for meas2
            verbose: [int] Flag to turn on intermediate product reporting during function call
                0 = no writes to the console
                1 = all step's intermediate values displayed on console
                2 = only final step solution parameters displayed on console
                3 = TBD measurements and model positions sent to console
            output_dir: [str] Path to the output directory where the Trajectory report and 'pickled' object
                will be stored.
            show_plots: [bool] Show plots of residuals, velocity, lag, meteor position. True by default.
            traj_id: [str] Trajectory solution identifier.
            comment: [str] Arbitrary string that can be saved.
        """

        # Init input parameters
        self.maxcameras = maxcameras
        self.jdt_ref = jdt_ref
        self.max_toffset = max_toffset
        self.nummonte = nummonte
        self.verbose = verbose
        self.meastype = meastype

        velmodel = str(velmodel)

        # Check if the initial velocity should be computed as the average velocity of the first half
        if 'fha' in velmodel:

            self.fha_velocity = True

            velmodel = int(velmodel.replace('fha', ''))

        else:
            self.fha_velocity = False

            velmodel = int(velmodel)


        self.velmodel = velmodel


        # Directory where the trajectory estimation results will be saved
        self.output_dir = output_dir

        # If True, plots will be shown on screen when the trajectory estimation is done
        self.show_plots = show_plots

        self.save_results = save_results

        # Trajectory solution identifier
        self.traj_id = str(traj_id)

        # Add the comment
        self.comment = comment



        ######################################################################################################


        # Track the number of measurements per each camera
        self.nummeas_lst = []

        # Track the station IDs of each camera
        self.station_ids = []

        # Track the obs_id of each camera
        self.obs_ids = []

        # Track the comment associated with each camera
        self.station_comments = []

        # Track magnitudes
        self.magnitudes = []

        # Track begin & end in FOV flags
        self.fov_beg = []
        self.fov_end = []

        # Construct a file name for this event
        self.file_name = jd2Date(self.jdt_ref, dt_obj=True).strftime('%Y%m%d_%H%M%S')

        ###
        # Init trajectory results (WARNING, there are more results to be read in, these are just some chosen
        # parameters. Please take a look at TrajectorySolution.h file and the TrajectoryInfo structure for
        # more solution parameters.)

        # Tracks the number of cameras actually populated by the solver library
        self.num_cameras = 0

        # Arrays of camera coordinates (angles in radians, height in km), per every station
        self.camera_lat = 0
        self.camera_lon = 0
        self.camera_hkm = 0

        # Best solution vector of the parameter values of length numparams
        # { Rx, Ry, Rz, Vx, Vy, Vz, Decel1, Decel2, tzero, tref_offsets[*] }
        # Note that R and V are in ECI (ECEF), in kilometers
        self.solution = 0

        # Convergence angle
        self.max_convergence = 0

        # intersecting planes radiant
        self.ra_radiant_ip = 0
        self.dec_radiant_ip = 0

        # Radiant right ascension in radians (multi-parameter fit)
        self.ra_radiant = 0
        # Radiant declination in radians (multi-parameter fit)
        self.dec_radiant = 0
        # Meteor solution velocity at the begin point in km/sec
        self.vbegin = 0
        # Average velocity
        self.vavg = 0
        # Deceleration term 1 defined by the given velocity model
        self.decel1 = 0
        # Deceleration term 2 defined by the given velocity model
        self.decel2 = 0

        # Standard deviation of radiant right ascension in radians
        self.ra_sigma = 0
        # Standard deviation of radiant declination in radians
        self.dec_sigma = 0
        # Standard deviation of vbegin in km/sec
        self.vbegin_sigma = 0
        # Standard deviation of decceleration term 1
        self.decel1_sigma = 0
        # Standard deviation of decceleration term 2
        self.decel2_sigma = 0

        # Array of geodetic latitudes closest to trail for each measurement
        self.meas_lat = 0
        # Array of +east longitudes closest to trail for each measurement
        self.meas_lon = 0
        # Array of heights re WGS84 closest to trail for each measurement
        self.meas_hkm = 0
        # Array of ranges from site along measurement to the CPA of the trail
        self.meas_range = 0
        # Array of velocity along the trail for each measurement
        self.meas_vel = 0

        # Array of geodetic latitudes for the model positions
        self.model_lat = 0
        # Array of +east longitudes for the model positions
        self.model_lon = 0
        # Array of heights re WGS84 for the model positions
        self.model_hkm = 0
        # Array of ranges from site to the model positions
        self.model_range = 0
        # Array of velocity on the trail at each model position
        self.model_vel = 0

        # Array of timing offsets in seconds
        self.tref_offsets = 0

        ## Model fit vectors which follow the same "meastype" on output with dimension #cameras x #measurements(camera)
        # Array of 1st data sequence containing the model fit in meastype format
        self.model_fit1 = 0
        # Array of 2nd data sequence containing the model fit in meastype format
        self.model_fit2 = 0
        # Array of model time which includes offsets relative to the reference time
        self.model_time = 0

        # computed begin point
        self.rbeg_lat = 0
        self.rbeg_lon = 0
        self.rbeg_ht = 0
        self.rbeg_lat_sigma = 0
        self.rbeg_lon_sigma = 0
        self.rbeg_hkm_sigma = 0

        # computed begin point
        self.rend_lat = 0
        self.rend_lon = 0
        self.rend_ht = 0
        self.rend_lat_sigma = 0
        self.rend_lon_sigma = 0
        self.rend_hkm_sigma = 0

        ########



        ### Calculated values

        # Position of the state vector in ECI coordinates (in meters)
        self.state_vect = None

        # ECI position of the radiant
        self.radiant_eci = None

        # List containing time data from each station
        self.times = None

        # List containing Julian dates of observations from each station
        self.JD_data_cameras = None

        # List containing ECI coordinates of the stations, as they moved through time
        self.stations_eci_cameras = None

        # List containing RA, Dec of every observation, from each station
        self.ra_dec_los_cameras = None

        # ECI coordinates calculated from ra_dec_los_cameras, from each station
        self.meas_eci_los_cameras = None

        # Calculated instantaneous velocities from each station
        self.velocities = None

        # Calculated lengths from each station
        self.lengths = None

        # Calculated distance from the beginning of the trajectory for every station
        self.state_vector_distances = None

        # Calculated lag from length and the initial velocity
        self.lags = None

        # Orbit solutions
        self.orbit = None

        # Uncertainties (currently not used!)
        self.uncertainties = None

        # use the default pso config file shipped with the module if none is specified
        if pso_config is None:
            self.pso_config = os.path.join(os.path.dirname(__file__), PSO_CONFIG_PATH)
        else:
            self.pso_config = pso_config

        ######


        # Load the trajectory library
        self.traj_lib = npct.load_library(TRAJ_LIBRARY, os.path.dirname(__file__))


        ### Define trajectory function types and argument types ###
        ######################################################################################################


        self.traj_lib.MeteorTrajectory.restype = ct.c_int
        self.traj_lib.MeteorTrajectory.argtypes = [
            ct.POINTER(TrajectoryInfo)
        ]


        self.traj_lib.InitTrajectoryStructure.restype = ct.c_void_p
        self.traj_lib.InitTrajectoryStructure.argtypes = [
            ct.c_int,
            ct.POINTER(TrajectoryInfo)
        ]


        self.traj_lib.ReadTrajectoryPSOconfig.restype = ct.c_void_p
        self.traj_lib.ReadTrajectoryPSOconfig.argtypes = [
            ct.POINTER(ct.c_char),
            ct.POINTER(TrajectoryInfo)
        ]


        self.traj_lib.FreeTrajectoryStructure.restype = ct.c_void_p
        self.traj_lib.FreeTrajectoryStructure.argtypes = [
            ct.POINTER(TrajectoryInfo)
        ]


        self.traj_lib.ResetTrajectoryStructure.restype = ct.c_void_p
        self.traj_lib.ResetTrajectoryStructure.argtypes = [
            ct.c_double,
            ct.c_double,
            ct.c_int,
            ct.c_int,
            ct.c_int,
            ct.c_int,
            ct.POINTER(TrajectoryInfo)
        ]


        self.traj_lib.InfillTrajectoryStructure.restype = ct.c_void_p
        self.traj_lib.InfillTrajectoryStructure.argtypes = [
            ct.c_int,
            PDOUBLE,
            PDOUBLE,
            PDOUBLE,
            PDOUBLE,
            ct.c_double,
            ct.c_double,
            ct.c_double,
            ct.POINTER(TrajectoryInfo)
        ]

        ######################################################################################################


        # Init the trajectory structure
        self.traj = TrajectoryInfo()
        self.traj_lib.InitTrajectoryStructure(maxcameras, self.traj)

        # Read PSO parameters
        self.traj_lib.ReadTrajectoryPSOconfig(self.pso_config.encode('ascii'), self.traj)

        # Reset the trajectory structure
        self.traj_lib.ResetTrajectoryStructure(jdt_ref, max_toffset, velmodel, nummonte, meastype, verbose,
            self.traj)



    def infillTrajectory(self, theta_data, phi_data, time_data, lat, lon, ele, noise=None, magnitudes=None,
        station_id=None, obs_id=None, fov_beg=None, fov_end=None, comment=None):
        """ Fills in the trajectory structure with given observations: azimuth in radians, zenith angle in
            radians, time in seconds relative to jdt_ref. This function should be called for each observing
            site, not more than 'maxcameras' times.

        Arguments:
            theta_data: [ndarray] array of azimuth angles (radians)
            phi_data: [ndarray] array of zenith angles (radians)
            time_data: [ndarray] time in seconds relative to jdt_ref
            lat: [float] latitude of the observing site (radians)
            lon: [float] longitude of the observing site (radians)
            ele: [float] height of the observing site (meters)

        Kwargs:
            noise: [ndarray] observation noise in radians (0 if not provided)
            magnitudes: [list] A list of apparent magnitudes of the meteor. None by default.
            station_id: [str] Station ID.
            obs_id: [int] Unique ID of the observation. This is to differentiate different observations from
                the same station.
            comment: [str] A comment about the observations.
        """

        nummeas = time_data.shape[0]

        # Track the number of measurement per each site
        self.nummeas_lst.append(nummeas)

        # Track the station ID of each site
        self.station_ids.append(station_id)

        # Track the obs_id of each site
        self.obs_ids.append(obs_id)

        # Track the magnitudes of each site
        self.magnitudes.append(magnitudes)

        # Track the beg/end fov flags
        self.fov_beg.append(fov_beg)
        self.fov_end.append(fov_end)

        # Track the comment of each site
        if comment is None:
            comment = ''

        self.station_comments.append(comment)

        # If the measurement noise is not given, set it to 0
        if noise is None:
            noise = np.zeros_like(time_data)*0.0

        # Fill the trajectory structure for site 1
        self.traj_lib.InfillTrajectoryStructure(nummeas, npct.as_ctypes(theta_data),
            npct.as_ctypes(phi_data), npct.as_ctypes(time_data), npct.as_ctypes(noise), lat, lon, ele/1000.0,
            self.traj)



    def calcVelocity(self):
        """ Convert meteor positions in ECI coordinates, calculate length at every point and the instantaneous
            velocity.
        """

        self.stations_eci_cameras = []
        self.times = []
        self.JD_data_cameras = []


        # Calculate positions of stations in ECI coordinates, for every point on the meteor's trajectory
        for kmeas, (lat, lon, hkm) in enumerate(zip(self.camera_lat, self.camera_lon, self.camera_hkm)):

            station_pos = []

            # Calculate time with time offsets included
            time_data = self.dtime[kmeas] + self.tref_offsets[kmeas]

            self.times.append(time_data)

            # Calculate Julian date
            jd_data = self.jdt_ref + time_data/86400.0

            self.JD_data_cameras.append(jd_data)

            # Go though every point in time of the measurement
            for jd in jd_data:

                # Calculate the ECI position of the station at the given time
                x, y, z = geo2Cartesian(lat, lon, hkm*1000.0, jd)

                station_pos.append([x, y, z])


            self.stations_eci_cameras.append(station_pos)


        ### Get the RA/Dec for each measurement

        self.ra_dec_los_cameras = []
        self.meas_eci_los_cameras = []

        # Go through each station:
        for jd_data, meas1, meas2, lat, lon in zip(self.JD_data_cameras, self.meas1, self.meas2, \
            self.camera_lat, self.camera_lon):

            # If inputs are RA and Dec
            if self.meastype == 1:

                ra_data = meas1
                dec_data = meas2

                # Calculate azimuth and elevation
                azim_data, elev_data = raDec2AltAz_vect(ra_data, dec_data, self.jdt_ref, lat, lon)


            # If inputs are azimuth +east of due north, and elevation angle
            elif self.meastype == 2:

                azim_data = meas1
                elev_data = meas2

            # If inputs are azimuth +west of due south, and zenith angle
            elif self.meastype == 3:

                azim_data = (meas1 + np.pi)%(2*np.pi)
                elev_data = np.pi/2.0 - meas2

            # If input are azimuth +north of due east, and zenith angle
            elif self.meastype == 4:

                azim_data = (np.pi/2.0 - meas1)%(2*np.pi)
                elev_data = np.pi/2.0 - meas2


            # Calculate RA and declination for the line of sight method
            ra_data_los, dec_data_los = altAz2RADec_vect(azim_data, elev_data, jd_data, lat, lon)

            self.ra_dec_los_cameras.append([ra_data_los, dec_data_los])


            # Calculate ECI coordinates of RA and Dec
            stat_los_eci = np.array(raDec2ECI(ra_data_los, dec_data_los)).T

            self.meas_eci_los_cameras.append(stat_los_eci)



        ######

        self.velocities = []
        self.lengths = []
        self.state_vector_distances = []

        rad_cpa_all_stations = []
        max_ht = 0
        beg_cpa = None

        # Calculate ECI coordinates of all LoS projections on the trajectory
        for stat_eci_los, meas_eci_los in zip(self.stations_eci_cameras, self.meas_eci_los_cameras):

            rad_cpa_list = []

            # Go through all individual position measurement from each site
            for stat, meas in zip(stat_eci_los, meas_eci_los):

                # Calculate closest points of approach (observed line of sight to radiant line)
                _, rad_cpa, _ = findClosestPoints(stat, meas, self.state_vect, self.radiant_eci)

                # Save ECI coordinates of the projection on the trajectory
                rad_cpa_list.append(rad_cpa)

                # Compute the distance from the centre of the Earth
                ht_center = vectMag(rad_cpa)

                # Save the ECI coordinate as the beginning if it has the highest height
                if ht_center > max_ht:
                    max_ht = ht_center
                    beg_cpa = np.copy(rad_cpa)

            rad_cpa_all_stations.append(rad_cpa_list)


        ### Calculate lengths and instantaneous velocities for all stations
        for kmeas, rad_cpa_list in enumerate(rad_cpa_all_stations):

            # Calculate the time data
            time_data = self.times[kmeas]

            radiant_distances = []
            state_vector_distance = []

            for i, rad_cpa in enumerate(rad_cpa_list):

                # Take the position of the first point as the reference point
                if i == 0:
                    ref_point = np.copy(rad_cpa)

                # Calculate the distance from the first observed point to the projected point on the radiant line
                dist = vectMag(ref_point - rad_cpa)
                radiant_distances.append(dist)

                # Calculate the distance from the beginning of the trajectory to the projected point
                beg_dist = vectMag(beg_cpa - rad_cpa)
                state_vector_distance.append(beg_dist)



            # Convert the distances (length along the trail) into a numpy array
            length = np.array(radiant_distances)
            state_vector_distance = np.array(state_vector_distance)

            self.lengths.append(length)
            self.state_vector_distances.append(state_vector_distance)


            # Shift the radiant distances one element down (for difference calculation)
            dists_shifted = np.r_[0, length][:-1]

            # Calculate distance differences from point to point (first is always 0)
            dists_diffs = length - dists_shifted

            # Shift the time one element down (for difference calculation)
            time_shifted = np.r_[0, time_data][:-1]

            # Calculate the time differences from point to point
            time_diffs = time_data - time_shifted

            # Replace zeros in time by machine precision value to avoid division by zero errors
            time_diffs[time_diffs == 0] = np.finfo(np.float64).eps

            # Calculate velocity for every point
            velocity = dists_diffs/time_diffs

            self.velocities.append(velocity)


        # Compute the initial velocity as the average of the first half
        if self.fha_velocity:

            # Set the average velocity if the velocity model was the constant velocity model
            if int(self.velmodel) == 0:
                self.vavg = self.vbegin


            best_stddev = np.inf
            best_solution = None

            # If there are more than 2 stations, try all rejecting one until the best velocity fit is found
            for i in range(1, self.maxcameras + 1):

                use_indices = list(range(self.maxcameras))

                # If there are more than 2 stations, reject one station
                if self.maxcameras > 2 and (i != 0):
                    use_indices.pop(i - 1)


                filtered_times = [time_dat for i, time_dat in enumerate(self.times) if i in use_indices]
                filtered_svs = [sv_dat for i, sv_dat in enumerate(self.state_vector_distances) if i in use_indices]

                # Get a list of times and lengths from all stations
                times = np.array([t for time_dat in filtered_times for t in time_dat]).flatten()
                sv_dists = np.array([sv for sv_dat in filtered_svs for sv in sv_dat]).flatten()

                # Sort by time
                temp_arr = np.c_[times, sv_dists]
                temp_arr = temp_arr[np.argsort(temp_arr[:, 0])]
                times, sv_dists = temp_arr.T

                half_index = int(len(times)/2)
                times_half = times[:half_index]
                sv_dists_half = sv_dists[:half_index]

                # Fit a line to the first 50% of the points
                popt, pcov = scipy.optimize.curve_fit(lineFunc, times_half, sv_dists_half)

                # Compute the standard deviation of the fit
                intercept_stddev = np.sqrt(np.diag(pcov))[1]


                if intercept_stddev < best_stddev:
                    best_stddev = intercept_stddev
                    best_solution = popt


                # End if there are only 2 stations
                if self.maxcameras == 2:
                    break


            # Compute the begin velocity
            self.vbegin = best_solution[0]/1000


            # print(self.vbegin)

            # # Plot original points
            # for t, sv, dt in zip(self.times, self.state_vector_distances, self.tref_offsets):
            #     plt.scatter(t, sv, label='dt: {:.2f}'.format(dt))

            # # Plot fitted line
            # time_arr = np.linspace(np.min(times), np.max(times), 100)
            # plt.plot(time_arr, lineFunc(time_arr, *best_solution))

            # plt.xlabel('Time (s)')
            # plt.ylabel('Length (m)')

            # plt.legend()

            # plt.show()




    def calcLag(self):
        """ Calculates the lag. """

        self.lags = []

        # Go through every station
        for time_data, length in zip(self.times, self.lengths):

            # Find the lag intercept from the given slope (i.e. initial velocity)
            lag_line = fitLagIntercept(time_data, length, self.vbegin*1000.0)

            # Calculate the lag
            lag = length - lineFunc(time_data, *lag_line)

            self.lags.append(lag)



    def calcAverages(self):
        """ Calculate the average velocity, ECI position on the trajectory and the average JD. """

        # List of average velocities per each station
        v_avg_list = []

        # List of meteor ECI coordinates per each camera
        self.model_eci_cameras = []

        # Go though every observation from each camera
        for kmeas in range(self.maxcameras):

            length = self.lengths[kmeas]
            time_data = self.times[kmeas]

            # Calculate the average velocity from the current station
            v_avg_list.append((length[-1] - length[0])/(time_data[-1] - time_data[0]))


            eci_list = []

            # Calculate ECI coordinated for every point on the meteor's track
            for j in range(self.nummeas_lst[kmeas]):

                eci_list.append(geo2Cartesian(self.model_lat[kmeas][j], self.model_lon[kmeas][j],
                    1000*self.model_hkm[kmeas][j], self.JD_data_cameras[kmeas][j]))

            eci_list = np.array(eci_list)

            # Convert meteor geographical positions to ECI coordinates
            self.model_eci_cameras.append(eci_list)


        # Calculate the average velocity across all stations
        v_avg = np.mean(v_avg_list)

        # Calculate average ECI coordinate from all measurements
        eci_x = [eci_meas[0] for eci_stat_list in self.model_eci_cameras for eci_meas in eci_stat_list]
        eci_y = [eci_meas[1] for eci_stat_list in self.model_eci_cameras for eci_meas in eci_stat_list]
        eci_z = [eci_meas[2] for eci_stat_list in self.model_eci_cameras for eci_meas in eci_stat_list]

        eci_avg = np.array([np.mean(eci_x), np.mean(eci_y), np.mean(eci_z)])

        # Calculate average JD date
        jd_avg = np.mean([np.mean(jd_data) for jd_data in self.JD_data_cameras])

        return v_avg, eci_avg, jd_avg





    def showPlots(self):
        """ Show plots of the solution. """


        ### PLOT RESIDUALS

        # Go through every stations
        for i, (time_data, meas1, model1, meas2, model2) in enumerate(zip(self.times, self.meas1,
            self.model_fit1, self.meas2, self.model_fit2)):

            # Calculate angular deviations in azimuth and elevation
            elev_res = meas2 - model2
            azim_res = (np.abs(meas1 - model1)%(2*np.pi))*np.sin(meas2)

            # Calculate the angular residuals from the radiant line
            ang_res = np.sqrt(elev_res**2 + azim_res**2)

            # Recalculate the angular residuals to arcseconds
            ang_res = np.degrees(ang_res)*3600

            # Calculate the RMS of the residuals
            res_rms = round(np.sqrt(np.mean(ang_res**2)), 2)


            plt.scatter(time_data, ang_res, s=2, zorder=3, label='Station: ' + str(i + 1) + \
                ', RMS = {:.2f}'.format(res_rms))


        plt.title('Observed vs. Radiant LoS Residuals, all stations')

        plt.ylabel('Angle (arcsec)')
        plt.xlabel('Time (s)')

        plt.ylim(ymin=0)

        plt.grid()
        plt.legend()

        plt.show()

        ######


        ### PLOT LAGS

        for i, (time_obs, lag) in enumerate(zip(self.times, self.lags)):

            plt.plot(lag, time_obs, marker='x', zorder=3, label='Station: ' + str(i + 1))


        plt.title('Lags, all stations')

        plt.xlabel('Lag (m)')
        plt.ylabel('Time (s)')

        plt.legend()
        plt.grid()
        plt.gca().invert_yaxis()

        plt.show()

        ######



        ### PLOT VELOCITIES

        # Possible markers for velocity
        markers = ['x', '+', '.', '2']

        # Generate a list of colors to use for markers
        colors = cm.rainbow(np.linspace(0, 1 , len(self.times)))

        for i, (time_obs, vel_obs) in enumerate(zip(self.times, self.velocities)):

            # Plot the measured velocity
            plt.scatter(vel_obs[1:], time_obs[1:], c=colors[i], marker=markers[i%len(markers)], alpha=0.5,
                label='Measured, station: ' + str(i + 1), zorder=3)


        for i, (time_model, vel_model) in enumerate(zip(self.model_time, self.model_vel)):

            # Plot modelled velocity
            plt.plot(vel_model*1000.0, time_model, c=colors[i], zorder=3, alpha=0.5,
                label='Modelled, station: ' + str(i + 1))


        plt.gca().invert_yaxis()
        plt.legend()
        plt.grid()

        plt.show()



        ######



    def saveReport(self, dir_path, file_name, uncertainties=None, verbose=True, save_results=True):
        """
        Save the trajectory estimation report to file.
        This function loosely mimics the output of Trajectory.py

        Arguments:
            dir_path: [str] Path to the directory where the report will be saved.
            file_name: [str] Name of the report time.

        Keyword arguments:
            uncertainties: [MCUncertainties object] (UNUSED) Object contaning uncertainties of every parameter.
            verbose: [bool] Print the report to the screen. True by default.
            save_results: [bool] If True, the results will be saved to a file.
        """
        # Format longitude in the -180 to 180 deg range
        _formatLongitude = lambda x: (x + np.pi)%(2*np.pi) - np.pi

        out = 'Input measurement type: '

        # Write out measurement type
        if self.meastype == 1:
            out += 'Right Ascension for meas1, Declination for meas2, epoch of date\n'
        elif self.meastype == 2:
            out += 'Azimuth +east of due north for meas1, Elevation angle above the horizon for meas2\n'
        elif self.meastype == 3:
            out += 'Azimuth +west of due south for meas1, Zenith angle for meas2\n'
        elif self.meastype == 4:
            out += 'Azimuth +north of due east for meas1, Zenith angle for meas2\n'

        out += "\n"
        out += f'Reference JD: {self.jdt_ref:20.12f}\n'
        out += f'Time: {str(jd2Date(self.orbit.jd_ref, dt_obj=True))} UTC'

        out += '\n\n'

        # gural solver doesn't report all candidates, only selected plane intersection
        # and station ids used are not reported
        out += 'Plane intersections\n'
        out += '-------------------\n'
        out += 'Intersection 1 - Station IDs unavailable\n'
        out += f' Convergence Angle = {np.degrees(self.max_convergence):.5f} deg\n'
        out += f' R.A. = {np.degrees(self.ra_radiant_ip):>9.5f}'
        out += f'  Dec = {np.degrees(self.dec_radiant_ip):>+9.5f} deg\n'

        out += '\n'
        out += f'Best intersection: Station IDs unavailable with Qconv = {np.degrees(self.max_convergence):.2f} deg\n'

        out += '\n\n'

        out += 'Multi-parameter fit solution\n'
        out += '----------------------------\n'

        # gural solver doesn't provide uncertainties for these
        # values out of the solver are in km and km/s
        out += 'State vector (ECI, epoch of date):\n'
        out += f' X =  {self.solution[0] * 1000.0:11.2f} m\n'
        out += f' Y =  {self.solution[1] * 1000.0:11.2f} m\n'
        out += f' Z =  {self.solution[2] * 1000.0:11.2f} m\n'
        out += f' Vx = {self.solution[3] * 1000.0:11.2f} m/s\n'
        out += f' Vy = {self.solution[4] * 1000.0:11.2f} m/s\n'
        out += f' Vz = {self.solution[5] * 1000.0:11.2f} m/s\n'

        out += '\n'
        out += 'State vector covariance matrix (X, Y, Z, Vx, Vy, Vz):\n'
        out += ' Not available\n'

        out += '\n'
        out += 'Timing offsets (from input data)\n'

        for station_id, toffset in zip(self.station_ids, self.tref_offsets):
            out += f'{station_id:>14s}: {toffset:.6f}\n'

        if self.orbit is not None:
            out += '\n'
            out += 'Reference point on the trajectory\n'
            out += f'  Time: {str(jd2Date(self.orbit.jd_ref, dt_obj=True))} UTC\n'
            out += f'  Lat     = {np.degrees(self.orbit.lat_ref):>11.6f}\n'
            out += f'  Lon     = {np.degrees(_formatLongitude(self.orbit.lon_ref)):>+11.6}\n'
            out += f'  Ht      = {self.orbit.ht_ref}\n'
            out += f'  Lat geo = {np.degrees(self.orbit.lat_geocentric)}\n'

            # Write out orbital parameters
            out += '\n'
            out += repr(self.orbit)

            # We don't have the orbital covariance matrix so skip it

        out += '\n'
        out += 'Jacchia fit on lag:\n'
        out += ' Not available\n'

        # Once again heights are in km
        out += '\n'
        out += 'Begin point on the trajectory (error estimate at 1.0 sigma):\n'
        out += f'  Lat = {np.degrees(self.rbeg_lat):>11.6f}'
        out += f' +/- {np.degrees(self.rbeg_lat_sigma):6.4f} deg\n'
        out += f'  Lon = {np.degrees(_formatLongitude(self.rbeg_lon)):>+11.6f}'
        out += f' +/- {np.degrees(self.rbeg_lon_sigma):6.4f} deg\n'
        out += f'  Ht  = {self.rbeg_hkm * 1000.0:>11.2f}'
        out += f' +/- {self.rbeg_hkm_sigma * 1000.0:6.2f} m\n'

        out += 'End point on the trajectory:\n'
        out += f'  Lat = {np.degrees(self.rend_lat):>11.6f}'
        out += f' +/- {np.degrees(self.rend_lat_sigma):6.4f} deg\n'
        out += f'  Lon = {np.degrees(_formatLongitude(self.rend_lon)):>+11.6f}'
        out += f' +/- {np.degrees(self.rend_lon_sigma):6.4f} deg\n'
        out += f'  Ht  = {self.rend_hkm * 1000.0:>11.2f}'
        out += f' +/- {self.rend_hkm_sigma * 1000.0:6.2f} m\n'

        ### Write information about stations ###
        ######################################################################################################
        out += '\n'
        out += "Stations\n"
        out += "--------\n"

        # Note we only have a single global acceleration computation for the entire trajectory
        # not a Jacchia fit for each station's lags as we have with the pylig solver
        # we make do with what we have...
        out += '            ID, Ignored, Lon +E (deg), Lat +N (deg),  Ht (m),   Accel a1,   Accel a2,  Beg Ht (m),  End Ht (m), +/- Obs ang (deg), +/- V (m), +/- H (m), Persp. angle (deg), Weight, FOV Beg, FOV End, Comment\n'

        for cam in range(0, self.num_cameras):
            endpt = self.nummeas_lst[cam] - 1

            info = []
            info.append(f'{self.station_ids[cam]:>14s}')
            info.append(f'{0:>7d}')
            info.append(f'{np.degrees(self.camera_lon[cam]):>12.6f}')
            info.append(f'{np.degrees(self.camera_lat[cam]):>12.6f}')
            info.append(f'{self.camera_hkm[cam] * 1000.0:>7.2f}')
            info.append(f'{self.decel1:>10.6f}')
            info.append(f'{self.decel2:>10.6f}')
            info.append(f'{self.model_hkm[cam][0] * 1000.0:>11.2f}')
            info.append(f'{self.model_hkm[cam][endpt] * 1000.0:>11.2f}')
            info.append(f'{"None":>17s}')
            info.append(f'{"None":>9s}')
            info.append(f'{"None":>9s}')
            info.append(f'{"None":>18s}')
            info.append(f'{"None":>6s}')
            info.append(f'{str(self.fov_beg[cam]):>7s}')
            info.append(f'{str(self.fov_end[cam]):>7s}')
            info.append(f'{self.station_comments[cam]:s}')

            out += ', '.join(info) + '\n'

        ### Write information about individual points ###
        ######################################################################################################
        out += '\n'
        out += "Points\n"
        out += "------\n"

        out += " No, "
        out += "    Station ID, "
        out += " Ignore, "
        out += " Time (s), "
        out += "                  JD, "
        out += "    meas1, "
        out += "    meas2, "
        out += "Azim +E of due N (deg), "
        out += "Alt (deg), "
        out += "Azim line (deg), "
        out += "Alt line (deg), "
        out += "RA obs (deg), "
        out += "Dec obs (deg), "
        out += "RA line (deg), "
        out += "Dec line (deg), "
        out += "      X (m), "
        out += "      Y (m), "
        out += "      Z (m), "
        out += "Latitude (deg), "
        out += "Longitude (deg), "
        out += "Height (m), "
        out += " Range (m), "
        out += "Length (m), "
        out += "State vect dist (m), "
        out += "  Lag (m), "
        out += "Vel (m/s), "
        out += "Vel prev avg (m/s), "
        out += "H res (m), "
        out += "V res (m), "
        out += "Ang res (asec), "
        out += "AppMag, "
        out += "AbsMag"
        out += "\n"

        # A number of these fields were never implemented by the GuralTrajectory
        # solver and are thus omitted here (left as 'None')
        for cam in range(0, self.num_cameras):
            for pt in range(0, self.nummeas_lst[cam]):
                info = []
                info.append(f'{pt:3d}')
                info.append(f'{self.station_ids[cam]:>14s}')
                info.append(f'{0:>7d}')

                info.append(f'{self.model_time[cam][pt]:9.6f}')
                info.append(f'{self.JD_data_cameras[cam][pt]:20.12f}')

                info.append(f'{np.degrees(self.meas1[cam][pt]):9.5f}')
                info.append(f'{np.degrees(self.meas2[cam][pt]):9.5f}')

                # Not yet implemented - would need to convert from meas & model
                # (lat,lon) or ECI coords at each point
                info.append(f'{"None":>22s}')
                info.append(f'{"None":>9s}')
                info.append(f'{"None":>15s}')
                info.append(f'{"None":>14s}')
                info.append(f'{"None":>12s}')
                info.append(f'{"None":>13s}')
                info.append(f'{"None":>13s}')
                info.append(f'{"None":>14s}')

                for eci in range(0,3):
                    info.append(f'{self.model_eci_cameras[cam][pt][eci]:11.2f}')

                info.append(f'{np.degrees(self.model_lat[cam][pt]):14.6f}')
                info.append(f'{np.degrees(self.model_lon[cam][pt]):+15.6f}')
                info.append(f'{self.model_hkm[cam][pt] * 1000.0:10.2f}')
                info.append(f'{self.model_range[cam][pt] * 1000.0:10.2f}')

                info.append(f'{self.lengths[cam][pt]:10.2f}')
                info.append(f'{self.state_vector_distances[cam][pt]:19.2f}')
                info.append(f'{self.lags[cam][pt]:9.2f}')

                # velocities_prev_point was never implemented
                info.append(f'{self.velocities[cam][pt]:9.2f}')
                info.append(f'{"None":>18s}')

                # residual data are not available
                info.append(f'{"None":>9s}')
                info.append(f'{"None":>9s}')
                info.append(f'{"None":>14s}')

                if self.magnitudes[cam] is not None:
                    info.append(f'{self.magnitudes[cam][pt]:+6.2f}')
                else:
                    info.append(f'{"None":>6s}')

                info.append(f'{"None":>6s}')

                out += ", ".join(info) + '\n'

        out += '\n'

        out += 'Notes\n'
        out += '-----\n'
        out += '- Not all fields in this table are presently available using the GuralTrajectory solver, some may be left blank\n'
        out += '- Points that have not been taken into consideration when computing the trajectory have \'1\' in the \'Ignore\' column.\n'
        out += '- The time already has time offsets applied to it.\n'
        out += '- \'meas1\' and \'meas2\' are given input points.\n'
        out += '- X, Y, Z are ECI (Earth-Centered Inertial) positions of projected lines of sight on the radiant line.\n'
        out += '- Zc is the observed zenith distance of the entry angle, while the Zg is the entry zenith distance corrected for Earth\'s gravity.\n'
        out += '- Latitude (deg) and Longitude (deg) are in WGS84 coordinates, while Height (m) is in the EGM96 datum. There values are coordinates of each point on the radiant line.\n'
        out += '- Jacchia (1955) deceleration equation fit was done on the lag.\n'
        out += '- Right ascension and declination in the table are given in the epoch of date for the corresponding JD, per every point.\n'


        if verbose:
            print(out)

        # Save the report to a file
        if save_results:
            mkdirP(dir_path)

            with open(os.path.join(dir_path, file_name), 'w') as f:
                f.write(out)

        return out



    def run(self):
        """ Run the trajectory estimation. """

        # Run trajectory estimation
        self.traj_lib.MeteorTrajectory(self.traj)

        print('Running done! Reading out results...')

        ### Read out the results

        # The actual number of cameras populated
        self.num_cameras = self.traj.numcameras

        # Camera coordinates (angles in radians, height in km)
        self.camera_lat = double1pointerToArray(self.traj.camera_lat, self.maxcameras)
        self.camera_lon = double1pointerToArray(self.traj.camera_lon, self.maxcameras)
        self.camera_hkm = double1pointerToArray(self.traj.camera_hkm, self.maxcameras)



        # ECI coordinates of measurements
        self.meashat_ECI = double3pointerToArray(self.traj.meashat_ECI, self.maxcameras, self.nummeas_lst, 3)

        # Read out the trajectory solution
        self.solution = np.copy(npct.as_array((ct.c_double*(9+self.maxcameras)).from_address(ct.addressof(self.traj.solution.contents))))

        # Read out the radiant position (radians)
        self.ra_radiant = np.frombuffer(self.traj.ra_radiant, float)[0]
        self.dec_radiant = np.frombuffer(self.traj.dec_radiant, float)[0]

        # Read out the convergence angle (radians)
        self.max_convergence = np.frombuffer(self.traj.max_convergence, float)[0]

        # Read out the intersecting planes radiant (radians)
        self.ra_radiant_ip = np.frombuffer(self.traj.ra_radiant_IP, float)[0]
        self.dec_radiant_ip = np.frombuffer(self.traj.dec_radiant_IP, float)[0]

        # Read out beginning velocity
        self.vbegin = np.frombuffer(self.traj.vbegin, float)[0]

        # Read out deceleration terms
        self.decel1 = np.frombuffer(self.traj.decel1, float)[0]
        self.decel2 = np.frombuffer(self.traj.decel2, float)[0]

        # Standard deviations of the solution (calculated using Monte Carlo approach)
        self.ra_sigma = np.frombuffer(self.traj.ra_sigma, float)[0]
        self.dec_sigma = np.frombuffer(self.traj.dec_sigma, float)[0]
        self.vbegin_sigma = np.frombuffer(self.traj.vbegin_sigma, float)[0]
        self.decel1_sigma = np.frombuffer(self.traj.decel1_sigma, float)[0]
        self.decel2_sigma = np.frombuffer(self.traj.decel2_sigma, float)[0]

        # Read out the measurement coordinates
        self.meas1 = double2pointerToArray(self.traj.meas1, self.maxcameras, self.nummeas_lst)
        self.meas2 = double2pointerToArray(self.traj.meas2, self.maxcameras, self.nummeas_lst)
        self.dtime = double2pointerToArray(self.traj.dtime, self.maxcameras, self.nummeas_lst)
        self.meas_lat = double2pointerToArray(self.traj.meas_lat, self.maxcameras, self.nummeas_lst)
        self.meas_lon = double2pointerToArray(self.traj.meas_lon, self.maxcameras, self.nummeas_lst)
        self.meas_hkm = double2pointerToArray(self.traj.meas_hkm, self.maxcameras, self.nummeas_lst)
        self.meas_range = double2pointerToArray(self.traj.meas_range, self.maxcameras, self.nummeas_lst)
        self.meas_vel = double2pointerToArray(self.traj.meas_vel, self.maxcameras, self.nummeas_lst)

        # Read in the time differences
        self.tref_offsets = double1pointerToArray(self.traj.tref_offsets, self.maxcameras)

        # Read of modeled coordinates
        self.model_lat = double2pointerToArray(self.traj.model_lat, self.maxcameras, self.nummeas_lst)
        self.model_lon = double2pointerToArray(self.traj.model_lon, self.maxcameras, self.nummeas_lst)
        self.model_hkm = double2pointerToArray(self.traj.model_hkm, self.maxcameras, self.nummeas_lst)
        self.model_range = double2pointerToArray(self.traj.model_range, self.maxcameras, self.nummeas_lst)
        self.model_vel = double2pointerToArray(self.traj.model_vel, self.maxcameras, self.nummeas_lst)

        # Read out vectors of modeled data (model time is relative to jdt_ref)
        self.model_fit1 = double2pointerToArray(self.traj.model_fit1, self.maxcameras, self.nummeas_lst)
        self.model_fit2 = double2pointerToArray(self.traj.model_fit2, self.maxcameras, self.nummeas_lst)
        self.model_time = double2pointerToArray(self.traj.model_time, self.maxcameras, self.nummeas_lst)

        # Read out begin point
        self.rbeg_lat = np.frombuffer(self.traj.rbeg_lat, float)[0]
        self.rbeg_lon = np.frombuffer(self.traj.rbeg_lon, float)[0]
        self.rbeg_hkm = np.frombuffer(self.traj.rbeg_hkm, float)[0]
        self.rbeg_lat_sigma = np.frombuffer(self.traj.rbeg_lat_sigma, float)[0]
        self.rbeg_lon_sigma = np.frombuffer(self.traj.rbeg_lon_sigma, float)[0]
        self.rbeg_hkm_sigma = np.frombuffer(self.traj.rbeg_hkm_sigma, float)[0]

        # Read out end point
        self.rend_lat = np.frombuffer(self.traj.rend_lat, float)[0]
        self.rend_lon = np.frombuffer(self.traj.rend_lon, float)[0]
        self.rend_hkm = np.frombuffer(self.traj.rend_hkm, float)[0]
        self.rend_lat_sigma = np.frombuffer(self.traj.rend_lat_sigma, float)[0]
        self.rend_lon_sigma = np.frombuffer(self.traj.rend_lon_sigma, float)[0]
        self.rend_hkm_sigma = np.frombuffer(self.traj.rend_hkm_sigma, float)[0]
        ###

        print('Freeing trajectory structure...')

        # Free memory for trajectory
        self.traj_lib.FreeTrajectoryStructure(self.traj)

        # Delete all library bindings and ctypes variables, so the object can be pickled
        del self.traj_lib
        del self.traj

        # Extract the state vector from the solution (convert to meters)
        self.state_vect = np.array(self.solution[:3]*1000.0)

        # Calculate ECI coordinates of the radiant
        self.radiant_eci = np.array(raDec2ECI(self.ra_radiant, self.dec_radiant))

        # Deceleration parameters
        self.decel = np.array(self.solution[6:8])

        print('RA:', np.degrees(self.ra_radiant))
        print('Dec:', np.degrees(self.dec_radiant))
        print('Vbeg:', self.vbegin)
        print('State vector:', self.state_vect)
        print('Deceleration:', self.decel)
        print('Timing offsets:', self.tref_offsets)


        # Calculate the ECI positions of the trajectory on the radiant line, length and velocities
        self.calcVelocity()

        # Calculate the lag
        self.calcLag()


        # Calculate average velocity and average ECI position of the trajectory
        v_avg, eci_avg, jd_avg = self.calcAverages()

        # Get the first Julian date of all observations
        jd_first = np.min([np.min(jd_data) for jd_data in self.JD_data_cameras])


        # Calculate the orbit
        self.orbit = calcOrbit(self.radiant_eci, self.vbegin*1000, v_avg, self.state_vect, jd_first,
            stations_fixed=False, reference_init=True)
        print(self.orbit)


        if self.show_plots:
            self.showPlots()


        ### SAVE RESULTS ###
        ######################################################################################################

        if self.fha_velocity:
            fha_suffix = 'fha'
        else:
            fha_suffix = ''

        # Save the picked trajectory structure with original points
        if self.save_results:
            savePickle(self, self.output_dir, self.file_name \
                + '_gural{:d}{:s}_trajectory.pickle'.format(self.velmodel, fha_suffix))


        ######################################################################################################


        return self



    def savePickle(self, dir_path, file_name):
        """
        pickle the object taking care to omit lingering unserializable
        trajectory library references
        """
        # copy and remove from the object
        # it's okay if these don't exist
        try:
            backup_traj_lib = self.traj_lib
            del self.traj_lib
        except AttributeError:
            backup_traj_lib = None

        try:
            backup_traj = self.traj
            del self.traj
        except AttributeError:
            backup_traj = None

        savePickle(self, dir_path, file_name)

        # ... and now restore them
        if backup_traj_lib:
            self.traj_lib = backup_traj_lib

        if backup_traj:
            self.traj = backup_traj



if __name__ == "__main__":



    ### TEST DATA
    maxcameras = 2

    # reference julian date
    jdt_ref = 2457660.770667

    # Velocity model type
    velmodel = 3

    # Print out all solution details (1) or not (0)
    verbose = 1

    # Measurements
    time1 = np.array([0.057753086090087891, 0.066874027252197266, 0.075989007949829102, 0.085109949111938477, 0.094237089157104492, 0.10335803031921387, 0.11248111724853516, 0.12160706520080566, 0.13072991371154785, 0.1398470401763916, 0.14896798133850098, 0.1580970287322998, 0.16721701622009277, 0.17634010314941406, 0.18546104431152344, 0.19459104537963867, 0.20371103286743164, 0.21282792091369629, 0.2219550609588623, 0.23107600212097168, 0.24019694328308105, 0.24931812286376953, 0.25844597816467285, 0.26756501197814941, 0.27669310569763184, 0.28580904006958008, 0.29493308067321777, 0.30405712127685547, 0.31317901611328125, 0.32230591773986816, 0.33142495155334473, 0.34055089950561523, 0.34967303276062012, 0.35879397392272949, 0.36792206764221191, 0.37704110145568848, 0.38615989685058594, 0.39528894424438477, 0.40440893173217773, 0.41353106498718262, 0.42265510559082031, 0.43178009986877441, 0.44089889526367188, 0.45002102851867676, 0.45915102958679199, 0.46827292442321777, 0.47739696502685547, 0.4865109920501709, 0.4956510066986084, 0.50475692749023438, 0.51387810707092285, 0.52300906181335449, 0.53212499618530273, 0.54124712944030762, 0.55037498474121094, 0.55949711799621582, 0.56861710548400879, 0.57773995399475098, 0.58686208724975586, 0.59599995613098145, 0.60510897636413574, 0.6142280101776123, 0.62335801124572754, 0.6324760913848877])
    phi1 = np.array([55.702480827431032, 55.793824368465614, 55.88753020599011, 55.980570544693705, 56.07327845058068, 56.16663811716176, 56.260021035671755, 56.351956828609609, 56.44505503179294, 56.538332186739993, 56.632552238675849, 56.725387680018272, 56.818000654246454, 56.911201723248155, 57.004115910036212, 57.097832261372453, 57.191412597398575, 57.283977960828018, 57.376746480149919, 57.470141289434302, 57.563543438121414, 57.656628924724671, 57.749937800483188, 57.842688174097987, 57.935406662789603, 58.028832186692952, 58.121575350363329, 58.214494019816144, 58.307473172753141, 58.400174495678399, 58.493023084887334, 58.586384750506248, 58.678949326179911, 58.771357833168224, 58.863850501356033, 58.956860447376158, 59.049171725989119, 59.141616797438303, 59.233887951111122, 59.326106753286169, 59.41819565787236, 59.510388361622056, 59.602333162218116, 59.694591678426015, 59.786106749232736, 59.877491613135611, 59.969101239628593, 60.06002031553944, 60.151680627553716, 60.242091556272477, 60.33297244327273, 60.423181062857637, 60.513186887636216, 60.602894553039164, 60.692293889528756, 60.780956442762005, 60.870177557670779, 60.958943574792976, 61.046938105117917, 61.134351245894756, 61.221159385330608, 61.306529365426798, 61.391267208416664, 61.467270929128503])
    theta1 = np.array([120.26319138247609, 120.22540695789934, 120.18673532370377, 120.14842794035015, 120.11034570859977, 120.0720842731949, 120.03390164839767, 119.99639652474744, 119.95850343282032, 119.92062401795023, 119.88244908946078, 119.84492055599212, 119.80756591352674, 119.77005822821938, 119.73274953746579, 119.69520274362065, 119.65779412056121, 119.6208730070672, 119.58395197967953, 119.54686324407939, 119.50985294580174, 119.47304858913093, 119.43623605212085, 119.39972291168513, 119.3633006386402, 119.32667935949192, 119.2904032683148, 119.25413572335212, 119.21792146205601, 119.1818915371656, 119.14588012264632, 119.10974570224057, 119.07399457396869, 119.03837757153748, 119.00280158839186, 118.96710031996264, 118.93173985985386, 118.89640024180805, 118.86119863355532, 118.82608798178681, 118.79109719190022, 118.75613703041395, 118.72134029918524, 118.6864941253839, 118.65199692326831, 118.61761616580844, 118.5832180249274, 118.54914529743198, 118.51486108335325, 118.48110902498068, 118.44724605387167, 118.41369719617806, 118.38028657342608, 118.34704871121083, 118.31398640380742, 118.28125669697118, 118.24838089786711, 118.21573228793197, 118.18342568415328, 118.15138963972852, 118.11963134239126, 118.08845334007034, 118.05755902182497, 118.02989359016864])

    time2 = np.array([0.0, 0.0091240406036376953, 0.018245935440063477, 0.027379035949707031, 0.036490917205810547, 0.045619010925292969, 0.05474090576171875, 0.063858985900878906, 0.072983026504516602, 0.082114934921264648, 0.091231107711791992, 0.10035109519958496, 0.10947489738464355, 0.11859893798828125, 0.12772512435913086, 0.13684391975402832, 0.14596700668334961, 0.15510010719299316, 0.16421103477478027, 0.17334103584289551, 0.18245911598205566, 0.19158196449279785, 0.20070290565490723, 0.20982694625854492, 0.2189481258392334, 0.22807097434997559, 0.23719310760498047, 0.24631595611572266, 0.25544309616088867, 0.26456212997436523, 0.27368307113647461, 0.28281092643737793, 0.29193806648254395, 0.30105209350585938, 0.31017804145812988, 0.31929898262023926, 0.32842206954956055, 0.33754897117614746, 0.34666705131530762, 0.3557898998260498, 0.36492395401000977, 0.37403392791748047, 0.38316202163696289, 0.39228296279907227, 0.40140891075134277, 0.41053390502929688, 0.41965007781982422, 0.42877292633056641, 0.43789410591125488, 0.44702005386352539, 0.45614290237426758, 0.46526408195495605, 0.47438502311706543, 0.48351693153381348, 0.49264097213745117, 0.50175309181213379, 0.51088404655456543, 0.52000308036804199, 0.52912497520446777, 0.53824996948242188, 0.54737210273742676, 0.55649089813232422, 0.56562089920043945, 0.5747380256652832, 0.58387494087219238, 0.592987060546875, 0.60210895538330078, 0.61122798919677734, 0.62035298347473145, 0.62947607040405273])
    phi2 = np.array([53.277395606543514, 53.378894622674743, 53.479956569118926, 53.581583564486643, 53.684034387407628, 53.785592745520816, 53.888221858788505, 53.989652095989705, 54.091286379162753, 54.193602941001174, 54.295489508972871, 54.39680492261197, 54.498476109777449, 54.600324950506916, 54.701540003200897, 54.803176858628973, 54.905770160432461, 55.007812728006726, 55.109255891578165, 55.210470634952003, 55.311514652098822, 55.413530094031998, 55.515323573286715, 55.616651349503798, 55.718365072619598, 55.81929890161981, 55.920171553847844, 56.021613048812512, 56.122821258097112, 56.224678899349627, 56.325865881424491, 56.426926299896216, 56.52861756575669, 56.629470224659684, 56.730172326265581, 56.831015465257991, 56.932197458064081, 57.033194520779368, 57.133991458819061, 57.234684773453658, 57.334955465097238, 57.435791110725937, 57.536108210586804, 57.63636328763743, 57.736907767451896, 57.837175586955425, 57.937203809457536, 58.036781893703278, 58.136978754564268, 58.236686044195643, 58.336377908906051, 58.43535465814314, 58.534625554011399, 58.6333660935654, 58.732691095623927, 58.831079484821906, 58.928886668384948, 59.026971367888081, 59.124250755486784, 59.221517041956538, 59.318507836373392, 59.414909920684529, 59.512434568208263, 59.608138099768297, 59.703628259049658, 59.799124823225796, 59.891752747734891, 59.983964699509606, 60.075068063440163, 60.161279720285414])
    theta2 = np.array([101.53457826463746, 101.51641844358608, 101.49838475808278, 101.48029817266169, 101.46211330853345, 101.44413445103872, 101.42601386963629, 101.40815190889943, 101.3903005255073, 101.37237603253595, 101.35457316211422, 101.3369156085497, 101.3192413904863, 101.30158154144411, 101.28407617303588, 101.26654229652355, 101.24888830028594, 101.23137351945253, 101.21400528102164, 101.19671926225118, 101.17950508630588, 101.16216841059016, 101.14491224554251, 101.12777721128521, 101.1106189746513, 101.09363369919245, 101.07669966495487, 101.05971116018192, 101.04280246671952, 101.02582610588094, 101.00900182871261, 100.99223844387629, 100.97541036501492, 100.95876039276899, 100.94217411607163, 100.92560326192354, 100.90901536078817, 100.89249613221422, 100.87604760647557, 100.85965363121295, 100.84336561880852, 100.82702299646236, 100.81080116796716, 100.79462576794111, 100.77843999684811, 100.76233476558444, 100.74630362442984, 100.73037973345876, 100.71439203286904, 100.69851722922239, 100.68267935704768, 100.66698899065921, 100.65128571495896, 100.63569963309027, 100.62005459779738, 100.60458982468012, 100.58924850257456, 100.57389559776691, 100.5587001573241, 100.5435378536578, 100.52844926863388, 100.51348253940968, 100.49837205688057, 100.48357341370303, 100.46883689073712, 100.45412830754735, 100.43988903856147, 100.42574044202429, 100.41178798399015, 100.39860839656382])

    # NOTE: For some reason, you can't do this: theta1 = npct.as_ctypes(theta1). For some reason, the memory
    # is not well allocated in that case...
    # The conversion must be done directly upon calling a function.

    # Convert measurement to radians
    theta1 = np.radians(theta1)
    phi1 = np.radians(phi1)

    theta2 = np.radians(theta2)
    phi2 = np.radians(phi2)

    ###

    ### SITES INFO

    lon1 = np.radians(-80.772090)
    lat1 = np.radians(43.264200)
    ele1 = 329.0

    lon2 = np.radians(-81.315650)
    lat2 = np.radians(43.192790)
    ele2 = 324.0



    ###


    # Init new trajectory solving
    traj_solve = GuralTrajectory(maxcameras, jdt_ref, velmodel, verbose=1)

    # Set input points for the first site
    traj_solve.infillTrajectory(theta1, phi1, time1, lat1, lon1, ele1)

    # Set input points for the second site
    traj_solve.infillTrajectory(theta2, phi2, time2, lat2, lon2, ele2)


    t1 = time.clock()

    # Solve the trajectory
    traj_solve.run()

    print('Run time:', time.clock() - t1)


    #sys.exit()

    ##########################################################################################################

    site_id = 0

    meas_x, meas_y, meas_z = sphericalToCartesian(traj_solve.meas_hkm[site_id]*1000, traj_solve.meas_lat[site_id], traj_solve.meas_lon[site_id])
    model_x, model_y, model_z = sphericalToCartesian(traj_solve.model_hkm[site_id]*1000, traj_solve.model_lat[site_id], traj_solve.model_lon[site_id])

    # Calculate the residual distance
    residual_dist = np.sqrt((meas_x - model_x)**2 + (meas_y - model_y)**2 + (meas_z - model_z)**2)

    plt.plot(time1, residual_dist)

    plt.ylim([0, 10])

    plt.show()
    plt.clf()



    plt.scatter(traj_solve.meas_vel[0], time1, marker='+')
    plt.scatter(traj_solve.meas_vel[1], time2, marker='x')

    plt.gca().invert_yaxis()

    plt.xlim([traj_solve.vbegin-3, traj_solve.vbegin+3])


    plt.show()




