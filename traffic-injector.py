import copy
import socket
import xmltodict
import configparser
import asyncio

def flight_xml_to_dict(flight_object:str) -> None:
    # Flight object contains one xml package for one flight
    xml = flight_object.replace('<?xml version="1.0" encoding="UTF-8"?>\n<NLROut source="NARSIM" xmlns:sti="http://www.w3.org/2001/XMLSchema-instance">', '')
    xml = xml.replace('</NLROut>', '')
    #print(xml)
    d = xmltodict.parse(xml)
    return d

def recv_narsim_data_packet(s: socket.socket, outer_partial_msg):
    msg = s.recv(1024)
    if (len(msg) < 1):
        return None

    # check: if contains '<?xml' <=> message is from Narsim
    if '<?xml' not in msg:
        raise Exception("<?xml not in msg received over tcp, is narsim connected?")

    # Add potential leftover from previous loop
    msg = outer_partial_msg + msg

    end_tag_count = msg.count('</NLROut>') # count occurance of </NLROut>
    inner_partial_msg = copy.copy(msg)
    dict_flights = []

    for i in range(end_tag_count):
        splt_str = inner_partial_msg.split('</NLROut>', 1)
        splt_str[0] += '</NLROut>'
        splt_str[1] = splt_str[1].strip('\n')
        flight_xml = splt_str[0]
        dict_flights.append(flight_xml_to_dict(flight_xml))
        inner_partial_msg = splt_str[1]
    outer_partial_msg = inner_partial_msg
    return outer_partial_msg, dict_flights

class ClientFlightsCounter():

    def __init__(self):
        self.flight_callsigns = []
        self.flight_objects = []

    def contains_callsign(self, callsign:str) ->  bool:
        return callsign in self.flight_callsigns

    def add_flight(callsign:str, object_ID: int) -> None:
        NotImplemented

    def update_flight(callsign:str) -> None:
        # set time at last update - such that it can be used to remove flights no longer updated
        NotImplemented

    def remove_flight(callsign:str) -> None:
        NotImplemented


async def traffic_injector_process(s:socket.socket) -> None:
    config = configparser.ConfigParser()
    config.read('config.conf')
    time_period = 1/config['traffic-injector']['frq']
    cfc = ClientFlightsCounter()

    dict_flights = []
    partial_msg = ''

    while True:
        partial_msg, dict_flights = recv_narsim_data_packet(s, partial_msg)
        for flight in dict_flights:
            callsign = flight['truth']['callsign']

            # if flight stored in client -> update
            if cfc.contains_callsign(callsign):
                # TODO
                cfc.update_flight(callsign)
            # else, create the flight
            else:
                # TODO
                cfc.add_flight(callsign)

            # if the narsim flight removed on narsim side -> delete | TODO

        asyncio.sleep(time_period)

