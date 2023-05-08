"""
This file includes code to retrieve data from msfs and feed it to Narsim.
"""
import socket
import time
import configparser
from SimConnect import SimConnect, AircraftRequests

# from SimConnect.Enum import *

# Constants
KNOTS_TO_METER_PER_SEC = 0.51444
FEET_TO_METER = 0.3048
METER_TO_FEET = 3.2808399
DEG_TO_RAD = 0.0174532925

# config for e.g. connections
config = configparser.ConfigParser()
config.read("./configuration.conf")

# connections
NARSIM_IP = config["NARSIM"]["IP"]
NARSIM_PORT = int(config["NARSIM"]["PORT"])

# flight plan
CALLSIGN = config["flightplan"]["CALLSIGN"]
ICAO_ID = int(config["flightplan"]["ICAO_ID"])
# SQUAWK_DECIMAL = str(int(config["flightplan"]["SQUAWK_OKTAL"], 8))
# print(SQUAWK_DECIMAL)
SQUAWK_OKTAL = config["flightplan"]["SQUAWK_OKTAL"]

# Time interval of user flight feeder
TIME_INTERVAL_USER_FLIGHT_FEEDER = 1 / float(
    config["user-flight-feeder"]["UPDATE_FREQUENCY"]
)

# Set to False in config for debug with MSFS only
SEND_DATA_TO_NARSIM = config["user-flight-feeder"]["SEND_DATA_TO_NARSIM"]
SEND_DATA_TO_NARSIM = SEND_DATA_TO_NARSIM == "True"

XML_TEMPLATE_TRUTH = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<NLRIn source="NARSIM" xmlns:sti="http://www.w3.org/2001/XMLSchema-instance">'
    '<truth><callsign>{callsign}</callsign><ssr_a>{ssr_a}</ssr_a><ssr_c>{ssr_c}</ssr_c><ssr_s>{ssr_s}</ssr_s><lat unit="deg">{lat}</lat>'
    '<lon unit="deg">{lon}</lon><alt unit="ft">{alt}</alt><height unit="m">{height}</height><gspd unit="ms">{gspd}</gspd>'
    '<crs unit="degrees">{crs}</crs><v_rate unit="ms">{v_rate}</v_rate><pitch>{pitch}</pitch><bank>{bank}</bank></truth></NLRIn>'
)
# TODO: the turn_rate sent to Narsim seems to be bugged and becomes a big value.
# #Seems to be ok sent from user_flight_feeder, but badly parsed in Narsim Gateway?
# Temporarily removed
# '<turn_rate unit="rad">{turn_rate}</turn_rate>
# TODO: further investigate altitudes, there is the indicated altitude, pressure altitude, etc.
# removed from xml: <height unit="ft">4000</height>


"""
TODO: add support to send Flight Plan to Narsim. Preliminary example xml template below.
"""
XML_TEMPLATE_FLIGHTPLAN = """<?xml version="1.0" encoding="UTF-8"?>
<NLRIn source="NARSIM" xmlns:sti="http://www.w3.org/2001/XMLSchema-instance">'
'<flightplan><callsign>SAS123</callsign><ssr_s>877777</ssr_s>'
'<clr_fl unit="ft">5000</clr_fl></flightplan></NLRIn>"""


"""
MSFS event variable names for SimConnect
Confirmed and working as of 29 nov 2022. Unit as received from SimConnect according to MSFS SDK docs.
"""
LAT_VARNAME = "PLANE_LATITUDE"  # [degrees]
LON_VARNAME = "PLANE_LONGITUDE"  # [degrees]
ALT_VARNAME = "PLANE_ALTITUDE"  # [ft]
HEIGHT_VARNAME = "PLANE_ALTITUDE"  # [ft]
GSPD_VARNAME = "GROUND_VELOCITY"  # [kts]
CRS_VARNAME = "PLANE_HEADING_DEGREES_TRUE"  # [radians]
V_RATE_VARNAME = "VERTICAL_SPEED"  # [feet per second]
TURN_RATE_VARNAME = "ROTATION_VELOCITY_BODY_Y"  # [feet per second] MSFS SDK doc typo?
LONG_ACC_VARNAME = "ACCELERATION_BODY_Z"  #  [feet per second2]
V_ACC_VARNAME = "ACCELERATION_BODY_Y"  # [feet per second2]
PITCH_VARNAME = (
    "PLANE_PITCH_DEGREES"  # [radians, "name mentions degrees, SDK says radians"]
)
BANK_VARNAME = (
    "PLANE_BANK_DEGREES"  # [radians, "name mentions degrees, SDK says radians"]
)
PRESSURE_ALTITUDE_VARNAME = "PRESSURE_ALTITUDE"


def connect_msfs() -> AircraftRequests:
    """Connect to MSFS

    Returns:
        AircraftRequests: object to request user aircraft data
    """
    try:
        simconnect = SimConnect()  # Obj for Connection to MSFS
        print("MSFS successfully connected!")
        ac_requests = AircraftRequests(simconnect)  # Obj for user a/c data requests
        return ac_requests, True

    except (ConnectionError, OSError):
        print("Unable to establish connection with MSFS. Make sure MSFS is running.")
        input("Press enter to attempt new connection.")
        return None, False


def connect_narsim(sock: socket.socket) -> bool:
    """make a connection to narsim
    TODO: investigate whether to move this function to top-level master file

    Args:
        sock (_type_): Socket for Narsim TCP communication

    Returns:
        bool: _description_
    """
    try:
        print("Trying to connect to Narsim...")
        sock.connect((NARSIM_IP, NARSIM_PORT))
        print("NARSIM successfully connected!")
        return True
    except socket.error as msg:
        print("Unable to connect to NARSIM, TCP socket error: " + msg)
        return None


def user_flight_feeder_main() -> None:
    """main loop"""

    # Initialize all as False
    msfs_is_connected = False
    msfs_data_stream_is_initialized = False
    narsim_is_connected = False

    # TODO: remove below when the "var_finder" is confirmed working
    # lat_finder = lon_finder = alt_finder = ""
    # gspd_finder = crs_finder = v_rate_finder = turn_rate_finder = ""
    # toa = lat = lon = alt = gspd = crs = v_rate = turn_rate = long_acc = v_acc = 0

    # Initialize time.
    last_time = time.time()

    # Main loop
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:

        while True:
            if not msfs_is_connected:
                ac_requests, msfs_is_connected = connect_msfs()

            if SEND_DATA_TO_NARSIM and (not narsim_is_connected):
                narsim_is_connected = connect_narsim(sock)

            current_time = time.time()
            if current_time >= last_time + TIME_INTERVAL_USER_FLIGHT_FEEDER:
                last_time = current_time
                toa = last_time

                if not msfs_data_stream_is_initialized:
                    try:
                        var_finder = {
                            "lat": ac_requests.find(LAT_VARNAME),
                            "lon": ac_requests.find(LON_VARNAME),
                            "alt": ac_requests.find(ALT_VARNAME),
                            "height": ac_requests.find(HEIGHT_VARNAME),
                            "gspd": ac_requests.find(GSPD_VARNAME),
                            "crs": ac_requests.find(CRS_VARNAME),
                            "v_rate": ac_requests.find(V_RATE_VARNAME),
                            "turn_rate": ac_requests.find(TURN_RATE_VARNAME),
                            "long_acc": ac_requests.find(LONG_ACC_VARNAME),
                            "v_acc": ac_requests.find(V_ACC_VARNAME),
                            "pitch": ac_requests.find(PITCH_VARNAME),
                            "bank": ac_requests.find(BANK_VARNAME),
                            "ssr_c": ac_requests.find(PRESSURE_ALTITUDE_VARNAME),
                        }

                        msfs_data_stream_is_initialized = True
                    except (ConnectionError, OSError):
                        print("Lost connection with MSFS. Unable to send TCP packet.")

                if msfs_data_stream_is_initialized:
                    try:
                        translation_dict = {
                            "callsign": CALLSIGN,
                            # "toa": toa,
                            "ssr_a": SQUAWK_OKTAL,
                            "ssr_c": int(
                                round(var_finder["ssr_c"].get() * METER_TO_FEET, -2)
                                / 100
                            ),
                            "ssr_s": ICAO_ID,
                            "lat": var_finder["lat"].get(),
                            "lon": var_finder["lon"].get(),
                            "alt": var_finder["alt"].get(),
                            "height": var_finder["height"].get() * FEET_TO_METER,
                            "gspd": var_finder["gspd"].get() * KNOTS_TO_METER_PER_SEC,
                            "crs": var_finder["crs"].get(),
                            "v_rate": var_finder["v_rate"].get() * FEET_TO_METER,
                            "turn_rate": var_finder["turn_rate"].get() * DEG_TO_RAD,
                            "long_acc": var_finder["long_acc"].get(),
                            "v_acc": var_finder["v_acc"].get(),
                            "pitch": -var_finder["pitch"].get(),
                            "bank": var_finder["bank"].get(),
                        }
                        xml_output = XML_TEMPLATE_TRUTH.format(**translation_dict)
                        print(xml_output)
                        # print(translation_dict["ssr_c"])
                        # print(translation_dict)
                        # print()

                        try:
                            if SEND_DATA_TO_NARSIM:
                                sock.send(xml_output.encode())
                        except socket.error as msg:
                            print("Lost connection to NARSIM, TCP socket error: " + msg)
                    except (ConnectionError, OSError):
                        print("Lost connection with MSFS.")
            time.sleep(0.10)  # lower load on CPU by not looping unnecessarily much


if __name__ == "__main__":
    user_flight_feeder_main()
