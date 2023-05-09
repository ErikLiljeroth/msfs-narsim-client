import socket
import time
from ctypes import c_char_p, c_double, pointer, sizeof
import configparser
import os
import copy
import asyncio
import xmltodict

from SimConnect import *
from SimConnect.Enum import SIMCONNECT_DATATYPE
from SimConnect.Constants import SIMCONNECT_UNUSED

# Constants
METER_TO_FEET = 3.2808399
METER_PER_SECOND_TO_KNOTS = 1.94384449


def flight_xml_to_dict(flight_object: str) -> dict:
    """Convert an xml flight object (one flight) from Narsim to a Python dictionary.

    Args:
        flight_object (str): xml string

    Returns:
        dict: dictionary of flight pos data
    """
    xml = flight_object.replace(
        '<?xml version="1.0" encoding="UTF-8"?>\n<NLROut source="NARSIM" xmlns:sti="http://www.w3.org/2001/XMLSchema-instance">',
        "",
    )
    xml = xml.replace("</NLROut>", "")
    # print(xml)
    flight_dict = xmltodict.parse(xml)
    return flight_dict


def recv_narsim_data_packet(
    narsim_socket: socket.socket, outer_partial_msg
) -> tuple[str, dict]:
    """Receive one packet of data from narsim, which could contain full data for one flight
    and partial data from other flights in the beginning and end of received string.

    Args:
        narsim_socket (socket.socket): the socket for tcp communication with narsim
        outer_partial_msg (_type_): left-over xml string from previous function call

    Raises:
        Exception: the functions scans for "<?xml", to verify that the message is from narsim

    Returns:
        tuple[str, dict]: left over xml string for next iteration and a list of dictionaries
                        with flight pos data for each parsed flight
    """

    msg = narsim_socket.recv(1024)
    if len(msg) < 1:
        return None

    # check: if contains '<?xml' <=> message is from Narsim
    if "<?xml" not in msg:
        raise Exception("<?xml not in msg received over tcp, is narsim connected?")

    # Add potential leftover from previous loop
    msg = outer_partial_msg + msg

    end_tag_count = msg.count("</NLROut>")  # count occurance of </NLROut>
    inner_partial_msg = copy.copy(msg)
    dict_flights = []

    for i in range(end_tag_count):
        splt_str = inner_partial_msg.split("</NLROut>", 1)
        splt_str[0] += "</NLROut>"
        splt_str[1] = splt_str[1].strip("\n")
        flight_xml = splt_str[0]
        dict_flights.append(flight_xml_to_dict(flight_xml))
        inner_partial_msg = splt_str[1]
    outer_partial_msg = inner_partial_msg
    return outer_partial_msg, dict_flights


def create_msfs_aircraft(
    sim_con: SimConnect,
    narsim_init_pos: dict,
    model_title: str = b"Airbus A320 Neo Asobo",
) -> tuple[int, int]:
    """Creates an aircraft object in MSFS using AICreateNonATCAircraft and
    returns the request ID and server-side created object_ID

    Args:
        sm (SimConnect): SimConnect object for managing MSFS API connection
        init_pos (dict): A dictionary containing keys
                        ['lat', 'lon', 'alt', 'pitch', 'bank', 'heading', 'on_ground', 'airspeed']
        model_title (str, optional): _description_. Defaults to b"Airbus A320 Neo Asobo".

    Returns:
        tuple[int, int]: returns the request id (req_id) and created object id
    """

    # get class definition for SIMCONNECT_DATA_INITPOSITION
    sm_types = sim_con.dll.AICreateNonATCAircraft.__ctypes_from_outparam__()
    init_position = sm_types.argtypes[3]

    req_id = sim_con.new_request_id()

    spawn_pos = init_position(
        c_double(narsim_init_pos["lat"]),
        c_double(narsim_init_pos["lon"]),
        c_double(narsim_init_pos["alt"]),
        c_double(narsim_init_pos["pitch"]),
        c_double(narsim_init_pos["bank"]),
        c_double(narsim_init_pos["heading"]),
        DWORD(narsim_init_pos["on_ground"]),
        DWORD(narsim_init_pos["airspeed"]),
    )
    sim_con.dll.AICreateNonATCAircraft(
        sim_con.hSimConnect, c_char_p(model_title), c_char_p(b"ABCD"), spawn_pos, req_id
    )
    time.sleep(0.07)
    # python-simmconnect adds created MSFS object_id as environment variable, bt it takes ~0.07sec
    return req_id, int(os.environ["SIMCONNECT_OBJECT_ID"])


def update_pos_msfs_aircraft(
    sim_con: SimConnect, narsim_flight_dict: dict, object_id: int
) -> bool:

    sim_con_types = sim_con.dll.AICreateNonATCAircraft.__ctypes_from_outparam__()
    init_position = sim_con_types.argtypes[3]

    updated_pos = init_position(
        c_double(float(narsim_flight_dict["truth"]["lat"]["#text"])),
        c_double(float(narsim_flight_dict["truth"]["lon"]["#text"])),
        c_double(float(narsim_flight_dict["truth"]["alt"]["#text"]) * METER_TO_FEET),
        c_double(float(narsim_flight_dict["truth"]["pitch"]["#text"])),  # <- pitch: added after fix from mbjork
        c_double(float(narsim_flight_dict["truth"]["bank"]["#text"])),  # <- bank: added after fix from mbjork
        c_double(int(narsim_flight_dict["truth"]["alt"]["#text"])),
        DWORD(
            0
            if float(narsim_flight_dict["truth"]["gspd"]["#text"])
            * METER_PER_SECOND_TO_KNOTS
            > 120
            else 1
        ),  # fix
        DWORD(
            float(narsim_flight_dict["truth"]["gspd"]["#text"])
            * METER_PER_SECOND_TO_KNOTS  # <- airspeed
        ),
    )

    sim_con.DEFINITION_POS = sim_con.new_def_id()
    sim_con.dll.AddToDataDefinition(
        sim_con.hSimConnect,
        sim_con.DEFINITION_POS.value,
        b"Initial Position",
        b"",
        SIMCONNECT_DATATYPE.SIMCONNECT_DATATYPE_INITPOSITION,
        0,
        SIMCONNECT_UNUSED,
    )

    sim_con_hr = sim_con.dll.SetDataOnSimObject(
        sim_con.hSimConnect,
        sim_con.DEFINITION_POS.value,
        object_id,
        0,
        0,
        sizeof(updated_pos),
        pointer(updated_pos),
    )

    return sim_con.IsHR(sim_con_hr, 0)


def delete_msfs_aircraft(sim_con: SimConnect, object_id: int) -> int:

    req_id = sim_con.new_request_id()
    sim_con.dll.AIRemoveObject(sim_con.hSimConnect, object_id, req_id)

    return req_id


class NarsimFlightsProcessor:
    def __init__(self):
        self.flight_callsigns = []
        self.flight_objects = []
        # flight object includes callsign, object_id, ac_model, ac_type

    def contains_callsign(self, callsign: str) -> bool:
        return callsign in self.flight_callsigns

    def get_object_id(self, callsign: str) -> int:
        """Get the object ID for flight 'callsign'

        Args:
            callsign (str): callsign of aircraft - must be unique from narsim

        Returns:
            int: object_ID if found otherwise None
        """
        return next(
            (item['object_id'] for item in self.flight_objects if item["callsign"] == callsign), None
        )

    def add_flight(self, callsign: str, object_ID: int) -> None:
        NotImplemented

    def update_flight(self, callsign: str) -> None:
        # set time at last update - such that it can be used to remove flights no longer updated
        NotImplemented

    def remove_flight(self, callsign: str) -> None:
        NotImplemented


async def traffic_injector_process(s: socket.socket, simc_injector: SimConnect) -> None:
    config = configparser.ConfigParser()
    config.read("config.conf")
    time_period_injector = 1 / config["traffic-injector"]["frq"]
    cfc = NarsimFlightsProcessor()
    ac_model = "Airbus A320 Neo Asobo"

    dict_flights = []
    partial_msg = ""

    while True:
        partial_msg, dict_flights = recv_narsim_data_packet(s, partial_msg)
        for flight in dict_flights:
            callsign = flight["truth"]["callsign"]

            # if flight stored in client -> update
            if cfc.contains_callsign(callsign):
                obj_id = cfc.get_object_id(callsign)
                if obj_id is None:
                    raise Exception(
                        "Tried to update pos of aircraft with MSFS object ID None"
                    )
                update_pos_msfs_aircraft(simc_injector, flight, obj_id)
                cfc.update_flight(callsign)  # TODO: update the time of update
            # else, create the flight
            else:
                # TODO
                # cfc.add_flight(callsign)
                pass
            # if the narsim flight removed on narsim side -> delete | TODO
            # Maybe the logic should be to see if an update has happened within x minutes else delete.

        asyncio.sleep(time_period_injector)


def main():
    # config
    HOST = "127.0.0.1"
    PORT = 5678
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((HOST, PORT))
        msg = s.recv(1024).decode()
        print(msg)

if __name__ == "__main__":
    main()