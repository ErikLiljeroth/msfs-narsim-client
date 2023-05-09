import socket
import time
import xmltodict
import copy
import collections
import os
import configparser
import signal

from SimConnect import *
from SimConnect.Enum import SIMCONNECT_DATATYPE
from SimConnect.Constants import SIMCONNECT_UNUSED

METER_TO_FEET = 3.2808399
METER_PER_SECOND_TO_KNOTS = 1.94384449
RAD_TO_DEG = 57.2957795

config = configparser.ConfigParser()
config.read("./configuration.conf")
TIME_INTERVAL_INJECTOR = 1/float(config['traffic_injector']['UPDATE_FREQUENCY_INJECTOR'])
# Set to False in config for debug with MSFS only
SEND_DATA_TO_MSFS = config["traffic_injector"]["SEND_DATA_TO_MSFS"]
SEND_DATA_TO_MSFS = SEND_DATA_TO_MSFS == "True"
CONSOLE_LOGGING_TIME_INTERVAL = config["traffic_injector"]["CONSOLE_LOGGING_TIME_INTERVAL"]

def create_msfs_aircraft(
    sim_con: SimConnect,
    flight_truth_dict: dict,
    model_title: str = b"Airbus A320 Neo Asobo",
) -> tuple[int, int]:
    f"""Creates an aircraft object in MSFS using AICreateNonATCAircraft and
    returns the request ID and server-side created object_ID

    Args:
        sm (SimConnect): SimConnect object for managing MSFS API connection
        flight_truth_dict (dict): A dict containing one dict accessed by key (callsign), and contains following nested keys:
                    ['ssr_s', 'ssr_c', 'ssr_a', 'lat', 'lon', 'height', 'alt', 'gspd', 'crs', 'v_rate', 'turn_rate', 'long_acc', 'v_acc', 'pitch', 'bank']
                    see function "transform_flight_dict"
        model_title (str, optional): _description_. Defaults to b"Airbus A320 Neo Asobo".

    Returns:
        tuple[int, int]: returns the request id (req_id) and created object id
    """
    # TODO: implement model matching
    # get class definition for SIMCONNECT_DATA_INITPOSITION
    sm_types = sim_con.dll.AICreateNonATCAircraft.__ctypes_from_outparam__()
    init_position = sm_types.argtypes[3]

    req_id = sim_con.new_request_id()

    callsign = list(flight_truth_dict.keys())[0]
    spawn_pos = init_position(
        c_double(flight_truth_dict[callsign]["lat"]["lat"]),
        c_double(flight_truth_dict[callsign]["lat"]["lon"]),
        c_double(flight_truth_dict[callsign]["lat"]["alt"]),
        c_double(flight_truth_dict[callsign]["lat"]["pitch"]),
        c_double(flight_truth_dict[callsign]["lat"]["bank"]),
        c_double(flight_truth_dict[callsign]["lat"]["crs"]), # TODO: should be heading, but crs entered instead
        DWORD(0), # onGround == 0 (inair) or == 1 (onGround)
        DWORD(flight_truth_dict[callsign]['gspd']), # TODO: Should be TAS, but gspd entered instead
    )
    sim_con.dll.AICreateNonATCAircraft(
        sim_con.hSimConnect, c_char_p(model_title), c_char_p(b"ABCD"), spawn_pos, req_id
    )
    time.sleep(0.07)
    # python-simmconnect adds created MSFS object_id as environment variable, bt it takes ~0.07sec
    return req_id, int(os.environ["SIMCONNECT_OBJECT_ID"])

def update_pos_msfs_aircraft(
    sim_con: SimConnect, flight_truth_dict: dict, object_id: int
) -> bool:

    sim_con_types = sim_con.dll.AICreateNonATCAircraft.__ctypes_from_outparam__()
    init_position = sim_con_types.argtypes[3]

    callsign = list(flight_truth_dict.keys())[0]
    updated_pos = init_position(
        c_double(float(flight_truth_dict[callsign]["lat"])),
        c_double(float(flight_truth_dict[callsign]["lon"])),
        c_double(float(flight_truth_dict[callsign]["alt"]) * METER_TO_FEET),
        c_double(float(flight_truth_dict[callsign]["pitch"])),  # <- pitch: added after fix from mbjork
        c_double(float(flight_truth_dict[callsign]["bank"])),  # <- bank: added after fix from mbjork
        c_double(int(flight_truth_dict[callsign]["alt"])),
        DWORD(
            0
            if float(narsim_flight_dict[callsign]["gspd"])
            * METER_PER_SECOND_TO_KNOTS
            > 100
            else 1
        ),  # TODO: make more sophisticated
        DWORD(
            float(flight_truth_dict[callsign]["gspd"])
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
    def __init__(self, sim_con:SimConnect = None):
        self.callsigns = set()
        # contains unique_callsigns
        self.meta = collections.defaultdict(dict)
        # flight meta includes callsign (key), object_id, ac_type, ac_model (for model matching), last_updated_truth_time (maybe move?)
        self.truth_data = collections.defaultdict(dict)
        # updated truth-data with callsign as key
        # TODO: save all object IDs to file and remove them when restarting. Otherwise maybe simobjects stay?
        if sim_con != None and SEND_DATA_TO_MSFS:
            self.sim_con = sim_con
        self.DEFAULT_AC_MODEL = b"Airbus A320 Neo Asobo"

    def contains_callsign(self, callsign: str) -> bool:
        return callsign in self.flight_callsigns

    def get_object_id(self, callsign: str) -> int:
        """Get the object ID for flight 'callsign' if exists

        Args:
            callsign (str): callsign of aircraft - must be unique from narsim

        Returns:
            int: object_ID if found otherwise None
        """
        if callsign not in self.meta:
            return None
        else:
            return self.meta[callsign]['object_id']

    def create_and_add_flight(self, callsign: str, flight_dict) -> None:
        self.callsigns.add(callsign)
        if SEND_DATA_TO_MSFS:
            _, object_id = create_msfs_aircraft(self.sim_con, flight_dict, model_title=self.DEFAULT_AC_MODEL)
            self.meta[callsign]['ac_type'] = 'A20N' # TODO: implemenent aircraft types from flight plan
            self.meta[callsign]['object_id'] = object_id # Object ID from MSFS to use ofr updating/deleting etc
            self.meta[callsign]['ac_model'] = self.DEFAULT_AC_MODEL # TODO: Implement model matching
            self.meta[callsign]['last_updated_truth_time'] = time.time() # top check how often data is updated
        self.truth_data.update(flight_dict)

    def update_flights(self, flight_dict_list) -> None:
        # TODO: set time at last update - such that it can be used to remove flights no longer updated
        for flight_dict in flight_dict_list:
            callsign = list(flight_dict.keys())[0]
            if callsign in self.callsigns:
                self.truth_data.update(flight_dict)
                if SEND_DATA_TO_MSFS:
                    update_pos_msfs_aircraft(self.sim_con, flight_dict, self.meta[callsign]['object_id'])
                self.meta[callsign]['last_updated_truth_time'] = time.time()
            else:
                # add flight and create in MSFS
                self.create_and_add_flight(callsign, flight_dict)

    def remove_flight(self, callsign: str) -> None:
        NotImplemented

    def remove_all(self) -> None:
        for cs in self.callsigns:
            if SEND_DATA_TO_MSFS:
                delete_msfs_aircraft(self.sim_con, self.meta[cs]['object_id'])
                time.sleep(0.07)
        print('all injected aircraft deleted in MSFS, success!')
        print('Closing...')

    def print_truth_state(self):
        list_of_dics = [value for value in self.truth_data.values()]
        for dic in list_of_dics:
            print(dic)
            print()

    def print_status_on_flights(self) -> None:
        print('---- INJECTOR STATUS ----')
        print('callsign, time of last data update')
        for cs in self.callsigns:
            localtime = time.strftime('%H:%M:%S', time.localtime(self.meta[cs]['last_updated_truth_time']))
            print(cs, localtime)
        print('-------------------------')


def transform_flight_dict(flight_dict:dict) -> dict:
    # this function handles unit conversion
    output = collections.defaultdict(dict)

    cs = flight_dict['truth']['callsign']
    ssr_s = int(flight_dict["truth"]["ssr_s"])
    ssr_c = int(flight_dict["truth"]["ssr_c"])
    ssr_a = int(flight_dict["truth"]["ssr_a"])
    lat = float(flight_dict["truth"]["lat"]["#text"])# lat
    lon = float(flight_dict["truth"]["lon"]["#text"])# lon
    height = float(flight_dict["truth"]["height"]["#text"]) * METER_TO_FEET# alt
    alt = float(flight_dict["truth"]["alt"]["#text"]) * METER_TO_FEET# alt
    gspd = float(flight_dict["truth"]["gspd"]["#text"]) * METER_PER_SECOND_TO_KNOTS # gspd
    crs = float(flight_dict["truth"]["crs"]["#text"]) * RAD_TO_DEG# crs
    v_rate = float(flight_dict["truth"]["v_rate"]["#text"]) # m/s
    turn_rate = float(flight_dict["truth"]["turn_rate"]["#text"]) # unit: None
    long_acc = float(flight_dict["truth"]["long_acc"]["#text"]) # unit: None
    v_acc = float(flight_dict["truth"]["v_acc"]["#text"]) # unit: None
    pitch = float(flight_dict["truth"]["pitch"]["#text"]) # pitch, no unit
    bank = float(flight_dict["truth"]["bank"]["#text"])# bank, no unit

    output[cs]['ssr_s'] = ssr_s
    output[cs]['ssr_c'] = ssr_c
    output[cs]['ssr_a'] = ssr_a
    output[cs]['lat'] = lat
    output[cs]['lon'] = lon
    output[cs]['height'] = height
    output[cs]['alt'] = alt
    output[cs]['gspd'] = gspd
    output[cs]['crs'] = crs
    output[cs]['v_rate'] = v_rate
    output[cs]['turn_rate'] = turn_rate
    output[cs]['long_acc'] = long_acc
    output[cs]['v_acc'] = v_acc
    output[cs]['pitch'] = pitch
    output[cs]['bank'] = bank

    return output

def truth_xml_2_dict(truth_xml: str) -> dict:
    """Convert an xml flight object (one flight) from Narsim to a Python dictionary.

    Args:
        truth_xml (str): xml string

    Returns:
        dict: dictionary of flight pos data
    """
    xml = truth_xml.replace(
        '<?xml version="1.0" encoding="UTF-8"?>\n    <NLROut source="NARSIM" xmlns:sti="http://www.w3.org/2001/XMLSchema-instance">',
        "",
    )
    xml = xml.replace("</NLROut>", "")
    flight_dict = xmltodict.parse(xml)
    return transform_flight_dict(flight_dict)

def flightplan_xml_2_dict(flightplan_xml: str) -> dict:
    # TODO: implement
    NotImplemented

def read_instant_all_narsim_data(s:socket) -> str:
    # the returned data is not decoded
    # "låt dig inspireras av gateway-logiken"
    data = b''
    while True:
        #print('test to read data')
        try:
            chunk = s.recv(1024)
            if not chunk:
                # No more data to receive (socket closed on other end), so break out of the loop
                break
            #print(f'chunk: {chunk}')
            data += chunk
        except socket.timeout:
            # A timeout occurred, so return an empty bytes object
            break
        time.sleep(0.05)
    return data.decode()

def parse_narsim_data(narsim_msg:str, outer_partial_msg):
    truth_output = []
    flightplan_output = [] # TODO: implement flightplan parsing

    # check: if contains '<?xml' <=> message is from Narsim
    if "<?xml" not in narsim_msg:
        raise Exception("<?xml not in msg received over tcp, is narsim connected?")

    # Add potential leftover from previous loop
    msg = outer_partial_msg + narsim_msg

    end_tag_count = msg.count("</NLROut>")  # count occurance of </NLROut> <=> nbr objects (flightplan/truth)
    inner_partial_msg = copy.copy(msg)

    for _ in range(end_tag_count):
        splt_str = inner_partial_msg.split("</NLROut>", 1)
        splt_str[0] += "</NLROut>"
        splt_str[1] = splt_str[1].strip("\n")
        xml_object = splt_str[0].strip()
        if '<truth>' in xml_object:
            truth_output.append(truth_xml_2_dict(xml_object))
        elif '<flightplan>' in xml_object:
            raise Exception('flightplan parsing not implemented')
        else:
            raise Exception(f'cannot recognize and parse narsim tag: {xml_object}')

        inner_partial_msg = splt_str[1]
    outer_partial_msg = inner_partial_msg

    return outer_partial_msg, truth_output

def exit_handler(signum, frame, injector_engine:NarsimFlightsProcessor):
    res = input("Ctrl+c was pressed. Do you really want to exit? y/n")
    if res == 'y':
        injector_engine.remove_all()
        time.sleep(0.1)
        exit(1)

signal.signal(signal.SIGINT, exit_handler)

def injector_process(sim_con: SimConnect = None, sock:socket = None):

    if SEND_DATA_TO_MSFS:
        injector_engine = NarsimFlightsProcessor(sim_con=sim_con)
    else:
        injector_engine = NarsimFlightsProcessor()

    socket_timeout_duration = 0.2
    sock.settimeout(socket_timeout_duration)

    # Initialize time.
    last_time = time.time()
    last_logging_time = time.time()

    outer_partial_msg = ''

    # Main loop
    while True:
        current_time = time.time()
        if current_time >= last_time + TIME_INTERVAL_INJECTOR:
            last_time = current_time

        # read data from NARSIM
        narsim_msg = read_instant_all_narsim_data(sock)
        outer_partial_msg, flight_list = parse_narsim_data(narsim_msg, outer_partial_msg)
        injector_engine.update_flights(flight_list)

        if current_time >= last_logging_time + CONSOLE_LOGGING_TIME_INTERVAL:
            last_logging_time = current_time
            injector_engine.print_status_on_flights()

        time.sleep(0.10)  # lower load on CPU by not looping unnecessarily much









def main2():
    out = collections.defaultdict(dict)
    test_str = '''<?xml version="1.0" encoding="UTF-8"?>
    <NLROut source="NARSIM" xmlns:sti="http://www.w3.org/2001/XMLSchema-instance"><truth><callsign>SAS940</callsign><toa>1605769267</toa><ssr_s>655387</ssr_s><ssr_c>370</ssr_c><ssr_a>1216</ssr_a><lat unit="rad">1.068793</lat><lon unit="rad">0.137567</lon><height unit="m">11320.176392</height><alt unit="m">11277.600000</alt><gspd unit="ms">241.956985</gspd><crs unit="rad">1.839705</crs><v_rate unit="ms">-0.002867</v_rate><turn_rate unit="">0.000000</turn_rate><long_acc unit="">-0.000000</long_acc><v_acc unit="">-0.000001</v_acc><pitch unit="">0.018409</pitch><bank unit="">0.001653</bank></truth></NLROut>
    <?xml version="1.0" encoding="UTF-8"?>
    <NLROut source="NARSIM" xmlns:sti="http://www.w3.org/2001/XMLSchema-instance"><truth><callsign>PNX652</callsign><toa>1605769267</toa><ssr_s>655375</ssr_s><ssr_c>11</ssr_c><ssr_a>1210</ssr_a><lat unit="rad">1.041327</lat><lon unit="rad">0.312816</lon><height unit="m">355.591098</height><alt unit="m">329.793600</alt><gspd unit="ms">64.025421</gspd><crs unit="rad">0.122003</crs><v_rate unit="ms">10.397296</v_rate><turn_rate unit="">0.000000</turn_rate><long_acc unit="">0.015880</long_acc><v_acc unit="">-0.009634</v_acc><pitch unit="">0.204837</pitch><bank unit="">0.000014</bank></truth></NLROut>
    <?xml version="1.0" encoding="UTF-8"?>
    <NLROut source="NARSIM" xmlns:sti="http://www.w3.org/2001/XMLSchema-instance"><truth><callsign>PNX652</callsign><toa>1605769268</toa><ssr_s>655375</ssr_s><ssr_c>11</ssr_c><ssr_a>1210</ssr_a><lat unit="rad">1.041337</lat><lon unit="rad">0.312818</lon><height unit="m">365.978767</height><alt unit="m">340.156800</alt><gspd unit="ms">64.043053</gspd><crs unit="rad">0.122005</crs><v_rate unit="ms">10.387669</v_rate><turn_rate unit="">0.000000</turn_rate><long_acc unit="">0.015862</long_acc><v_acc unit="">-0.009627</v_acc><pitch unit="">0.204737</pitch><bank unit="">0.000014</bank></truth></NLROut>
    <?xml version="1.0" encoding="UTF-8"?>
    <NLROut source="NARSIM" xmlns:sti="http://www.w3.org/2001/XMLSchema-instance"><truth><callsign>EWG370</callsign><toa>1605769268</toa><ssr_s>655373</ssr_s><ssr_c>389</ssr_c><ssr_a>1207</ssr_a><lat unit="rad">1.000812</lat><lon unit="rad">0.266291</lon><height unit="m">11894.820315</height><alt unit="m">11860.377600</alt><gspd unit="ms">230.154205</gspd><crs unit="rad">0.610865</crs><v_rate unit="ms">0.000000</v_rate><turn_rate unit="">0.000000</turn_rate><long_acc unit="">0.000000</long_acc><v_acc unit="">0.000000</v_acc><pitch unit="">0.000000</pitch><bank unit="">0.000000</bank></truth></NLROut>
    '''
    str, flights = parse_narsim_data(test_str, '')
    for flt in flights:
        #print()
        #print(list(flt.keys())[0])
        #print()
        out.update(flt)

    print(out)

def main():
    # config
    HOST = "127.0.0.1"
    PORT = 5683

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        socket_timeout_duration = 0.2
        s.settimeout(socket_timeout_duration)
        s.connect((HOST, PORT))
        data = read_instant_all_narsim_data(s)
        #print('----DATA----')
        #print(data)
        print('----DECODED---')
        print(data.decode())
        print('---------')

if __name__ == '__main__':
    main2()