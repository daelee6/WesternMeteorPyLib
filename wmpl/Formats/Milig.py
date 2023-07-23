""" Reading in MILIG input format and running the trajectory solver using an MILIG input file. """

from __future__ import print_function, absolute_import

import os
import sys
import argparse

import numpy as np

from wmpl.Formats.GenericFunctions import addSolverOptions
from wmpl.Trajectory.Trajectory import Trajectory
from wmpl.Trajectory.GuralTrajectory import GuralTrajectory
from wmpl.Utils.TrajConversions import date2JD, jd2Date, jd2LST


class StationData(object):
    """ Holds information loaded from the MILIG input file. """

    def __init__(self, station_id, lon, lat, height):

        self.station_id = station_id
        self.lon = lon
        self.lat = lat
        self.height = height

        self.time_data = []
        self.azim_data = []
        self.zangle_data = []
        self.ignore_picks = []


    def __repr__(self):
        """ Returns a string for printing the contents of this object. """

        out_str = ''

        out_str += 'Station ID: ' + str(self.station_id) + '\n'
        out_str += 'Longitude: ' + str(np.degrees(self.lon)) + ' deg\n'
        out_str += 'Latitude: ' + str(np.degrees(self.lat)) + ' deg\n'
        out_str += 'Height: ' + str(self.height) + ' m\n'

        out_str += '\n'
        out_str += 'Time (s), Azimuth +W of S (d), Zenith angle (d)\n'

        for time, azim, zangle in zip(self.time_data, self.azim_data, self.zangle_data):
            out_str += "{:8.6f}, {:19.6f},  {:16.6f}\n".format(time, np.degrees(azim), np.degrees(zangle))

        return out_str




def loadMiligInput(file_path):
    """ Loads MILIG-style input files. 
    
    Arguments:
        file_path: [str] path to the MILIG input file

    Return:
        [jd, stations]: [list] a list containing loaded info from the MILIG input file

    """

    with open(file_path) as f:

        # Set the file pointer to the beginning
        f.seek(0)

        #-> First line
        
        # Fireball date and time - this time is the reference time (t = 0) for all picks
        fireball_date = f.read(8)
        fireball_time = f.read(8)

        # Unpack the date and time
        year, month, date = fireball_date[:4], fireball_date[4:6], fireball_date[6:8]
        hh, mm, ss = fireball_time[:2], fireball_time[2:4], fireball_time[4:]

        time_list = list(map(float, [year, month, date, hh, mm, ss]))

        # Calculate the reference Julian date
        jdt_ref = date2JD(*time_list)

        # Greenwich Sidereal Time in degrees (NOT USED)
        gst = float(f.read(10))

        # Convergation control factor (NOT USED)
        ccf = f.read(10)

        f.readline()

        stations = []
        stat_ind = 0

        # Flag indicating that the next line to be read is the line with the new station
        new_station = True

        # Read in the rest of the lines
        for line in f:

            # Stop reading if '-1' flag has been reached
            if line.replace('\n', '').replace('\r', '') == '-1':
                break

            # If the last line kad the control character 9, this marks the beginning of new station picks
            if new_station:

                # Station ID
                station_id = line[:3].strip()

                # Station latitude in degrees, +N (converted to radians)                
                lat = np.radians(float(line[3:13]))

                # Station longitude in degrees, +E (converted to radians)
                lon = np.radians(float(line[13:23]))

                # Station heightation in kilometers (converted to meters)
                height = float(line[23:28])*1000

                # Weight of observations from this station (NOT USED)
                weight = line[28:33]

                # General comment for this station (NOT USED)
                comment = line[33:]

                print(station_id, lon, lat, height)

                # Initialize a new station
                station = StationData(station_id, lat, lon, height)
                
                # Add the station to the station list
                stations.append(station)

                # Set the proper station list index
                stat_ind = len(stations) - 1

                new_station = False

            else:
                # Read the position pick from the previous station

                # Azimuth +W of due South in degrees (converted to radians)
                azim = np.radians(float(line[:9]))

                # Zenith angle (converted to radians)
                zangle = np.radians(float(line[9:17]))

                # This number should be 9 if it is the last pick from this station
                last_pick = int(line[17:20])

                # Next line will containg information about a new station
                if last_pick == 9:
                    new_station = True

                
                # Flag indicating that the pick is bad. If it is 1, the pick will be ignored
                bad_pick = int(line[20:23])

                # Time in seconds from the reference GST
                time = float(line[23:31])

                # Add the time and coordinate to the station data
                stations[stat_ind].time_data.append(time)
                stations[stat_ind].azim_data.append(azim)
                stations[stat_ind].zangle_data.append(zangle)
                stations[stat_ind].ignore_picks.append(bad_pick)


            # There are 2 extra rows in the MILIG format which are not read by this parser, as they do not
            # contain information used by the solver in this library


        return [jdt_ref, stations]



def solveTrajectoryMILIG(dir_path, file_name, solver='original', **kwargs):
    """ Run the trajectory solver on data provided in the MILIG format input file. 

    Arguments:
        dir_path: [str] Directory where the MILIG input file is located.
        file_name: [str] Name of the MILIG input file.

    Keyword arguments:
        **kwargs: [dict] Additional keyword arguments will be directly passed to the trajectory solver.


    Return:
        None

    """

    # Load data from the MILIG input file
    jdt_ref, stations = loadMiligInput(os.path.join(dir_path, file_name))

    print('JD', jdt_ref)

    # Init the trajectory solver
    if solver == 'original':
        traj = Trajectory(jdt_ref, output_dir=dir_path, meastype=3, **kwargs)

    elif solver.startswith('gural'):

        # Extract velocity model is given
        try:
            velmodel = int(solver[-1])

        except: 
            # Default to the exponential model
            velmodel = 3

        traj = GuralTrajectory(len(stations), jdt_ref, velmodel=velmodel, meastype=3, verbose=1, 
            output_dir=dir_path)



    # Infill data from each station to the solver
    for station in stations:

        print(station)


        if solver == 'original':

            excluded_time = None


            # ### ADD A TIME OF EXCLUSION ###
            # # Add a time range for the given station for which there are not measurements (possibly due to
            # # saturation). This way the calculation of length residuals will not be affected.

            # if station.station_id == "1":
            #     print('EXCLUDED POINTS')
            #     #excluded_time = [0.580001, 0.859983]
            #     #excluded_time = [1.7, 3.2]

            ###############################

            traj.infillTrajectory(station.azim_data, station.zangle_data, station.time_data, station.lat, 
                station.lon, station.height, station_id=station.station_id, excluded_time=excluded_time, \
                ignore_list=station.ignore_picks)

        elif 'gural' in solver:

            traj.infillTrajectory(np.array(station.azim_data), np.array(station.zangle_data), 
                np.array(station.time_data), station.lat, station.lon, station.height)


        else:
            print('Solver: {:s} is not a valid solver!'.format(solver))


    # for obs in traj.observations:
    #     print(np.degrees(obs.lat), np.degrees(obs.lon))

    # Run the trajectory solver
    traj = traj.run()


    return traj



def writeMiligInputFile(jdt_ref, meteor_list, file_path, convergation_fact=1.0):
    """ Write the MILIG input file. 

    Arguments:
        jdt_ref: [float] reference Julian date.
        meteor_list: [list] A list of StationData objects
        file_path: [str] Path to the MILIG input file which will be written.

    Keyword arguments:
        convergation_fact: [float] Convergation control factor. Iteration is stopped when increments of all 
            parameters are smaller than a fixed value. This factor scales those fixed values such that 
            tolerance can be increased or decreased. By default, evcorr sets this to 0.01 (stricter 
            tolerances) and METAL uses 1.0 (default tolerances).

    Return:
        None
    """

    # Take the first station's longitude for the GST calculation
    lon = meteor_list[0].lon

    # Calculate the Greenwich Mean Time
    _, gst = jd2LST(jdt_ref, np.degrees(lon))

    datetime_obj = jd2Date(jdt_ref, dt_obj=True)


    with open(file_path, 'w') as f:

        datetime_str = datetime_obj.strftime("%Y%m%d%H%M%S.%f")[:16]

        # Write the first line with the date, GST and Convergation control factor
        f.write(datetime_str + '{:10.3f}{:10.3f}\n'.format(gst, convergation_fact))

        # Go through every meteor
        for meteor in meteor_list:

            # Write station ID and meteor coordinates. The weight of the station is set to 1
            f.write("{:3d}{:+10.5f}{:10.6f}{:5.3f}{:5.2f}\n".format(int(meteor.station_id), 
                np.degrees(meteor.lon), np.degrees(meteor.lat), meteor.height/1000.0, 1.0))

            # Go through every point in the meteor
            for i, (azim, zangle, t) in enumerate(zip(meteor.azim_data, meteor.zangle_data, \
                meteor.time_data)):

                last_pick = 0

                # If this is the last point, last_pick is 9
                if i == len(meteor.time_data) - 1:
                    last_pick = 9

                # Write individual meteor points. If the 4th column is 1, the point will be ignored.
                if t < 0:
                    time_format = "{:+8.5f}"
                else:
                    time_format = "{:8.6f}"
                f.write(("{:9.5f}{:8.5}{:3d}{:3d}" + time_format + "\n").format(np.degrees(azim), \
                    np.degrees(zangle), last_pick, 0, t))

        # Flag indicating that the meteor data ends here
        f.write('-1\n')

        # Initial aproximations
        f.write(' 0.0 0.0 0.0 0.0 0.0 0.0 0.0\n')

        # Optional parameters
        f.write('RFIX\n \n')






if __name__ == '__main__':

    ### COMMAND LINE ARGUMENTS

    # Init the command line arguments parser
    arg_parser = argparse.ArgumentParser(description=""" Run the Monte Carlo trajectory solver using MILIG files.""",
        formatter_class=argparse.RawTextHelpFormatter)

    arg_parser.add_argument('input_file', type=str, help='Path to the MILIG input file.')

    # Add other solver options
    arg_parser = addSolverOptions(arg_parser)

    # Parse the command line arguments
    cml_args = arg_parser.parse_args()

    ############################
    

    ### Parse command line arguments ###

    max_toffset = None
    print(cml_args.maxtoffset)
    if cml_args.maxtoffset:
        max_toffset = cml_args.maxtoffset[0]

    velpart = None
    if cml_args.velpart:
        velpart = cml_args.velpart

    vinitht = None
    if cml_args.vinitht:
        vinitht = cml_args.vinitht[0]

    ### ###

        
    # Split the input directory and the file
    if os.path.isfile(cml_args.input_file):

        dir_path, file_name = os.path.split(cml_args.input_file)

    else:
        print('Input file: {:s}'.format(cml_args.input_file))
        print('The given input file does not exits!')
        sys.exit()

    # Run the solver
    solveTrajectoryMILIG(dir_path, file_name, solver=cml_args.solver, max_toffset=max_toffset, \
        monte_carlo=(not cml_args.disablemc), mc_runs=cml_args.mcruns, geometric_uncert=cml_args.uncertgeom, \
        gravity_correction=(not cml_args.disablegravity), plot_all_spatial_residuals=cml_args.plotallspatial,
        plot_file_type=cml_args.imgformat, show_plots=(not cml_args.hideplots), v_init_part=velpart, \
        v_init_ht=vinitht, show_jacchia=cml_args.jacchia, \
        estimate_timing_vel=(False if cml_args.notimefit is None else cml_args.notimefit), \
        fixed_times=cml_args.fixedtimes, mc_noise_std=cml_args.mcstd)



    # ######

    # # dir_path = "../MirfitPrepare/20161007_052749_mir/"
    # # file_name = "input_00.txt"

    # #dir_path = "/home/dvida/Desktop/krizy"
    # #dir_path = '/home/dvida/Desktop/PyLIG_in_2011100809VIB0142'
    # #dir_path = "/home/dvida/Desktop/PyLIG_in_2011100809PUB0030"
    # #dir_path = "/home/dvida/Desktop/PyLIG_in_2011100809VIB0141"
    # #dir_path = "/home/dvida/Desktop/PyLIG_in_2011100809DUI0066"
    # #dir_path = "/home/dvida/Desktop/PyLIG_in_2016112223APO0002"
    # #dir_path = os.path.abspath("../MILIG files")
    # #dir_path = os.path.abspath("../MILIG files/20170531_002824")
    # #dir_path = os.path.abspath("../MILIG files/PyLIG_IN_Pula_2010102829")
    # #dir_path = os.path.abspath("../MILIG files/20170923_053525 meteorite dropping")
    # #dir_path = os.path.abspath("../MILIG files/20170923_053525 meteorite dropping GRAVITY TEST")
    # #dir_path = os.path.abspath("../MILIG files/20171127_meteorite_dropping")
    # #dir_path = os.path.abspath("../MILIG files/20171231_011853")
    # #dir_path = os.path.abspath("../MILIG files/20180125_meteorite_dropping")
    # #dir_path = os.path.abspath("../MILIG files/PyLIG20180123_020244")
    # #dir_path = os.path.abspath("../MILIG files/PyLIG20180206_011705")
    # #dir_path = os.path.abspath("../MILIG files/PyLIG20180209_231854")
    # #dir_path = os.path.abspath("../MILIG files/20180117_010828 Michigan fireball")
    # #dir_path = os.path.abspath("../MILIG files/20180117_010828 Michigan fireball (2 stations)")
    # dir_path = os.path.abspath("../MILIG files/20180117_010828 Michigan fireball (4 stations)")
    # #dir_path = os.path.abspath("../MILIG files/20180117_010828 Michigan fireball (2 stations) second")
    

    # #file_name = "input_krizy_01.txt"
    # #file_name = 'PyLIG_in_2011100809PUB0030.txt'
    # #file_name = 'PyLIG_in_2011100809DUI0066.txt'
    # #file_name = 'PyLIG_in_2011100809VIB0142.txt'
    # #file_name = "PyLIG_in_2011100809VIB0141.txt"
    # #file_name = "PyLIG_in_2016112223APO0002.txt"
    # #file_name = "PyLIG_IN_Pula_2010102829.txt"
    # #file_name = "PyLIG_M_20170531_002824.txt"
    # #file_name = "PyLIG_IN_Pula_2010102829.txt"
    # #file_name = "20170923_053525-obs.dat"
    # file_name = "input.txt"
    # #file_name = "20171231_011853-input.txt"
    # #file_name = "20180123_020244-input.txt"
    # #file_name = "20180206_011705_input.txt"
    # #file_name = "20180206_011705_input.txt"



    # solveTrajectoryMILIG(dir_path, file_name, solver='original', max_toffset=1.0, monte_carlo=False, 
    #     mc_runs=250, gravity_correction=True)

    


