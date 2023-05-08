import socket
import time
import xmltodict

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

def read_from_narsim_socket_once(s:socket):
    data = b''
    try:
        chunk = s.recv(1024)
        if not chunk:
            return b''
        return chunk
    except socket.timeout:
        return b''

def read_all_available_from_narsim_socket():
    pass

def receive_available_data(s:socket):
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
    return data



def main():
    # config
    HOST = "127.0.0.1"
    PORT = 5683

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        socket_timeout_duration = 0.2
        s.settimeout(socket_timeout_duration)
        s.connect((HOST, PORT))
        data = receive_available_data(s)
        #print('----DATA----')
        #print(data)
        print('----DECODED---')
        print(data.decode())
        print('---------')



if __name__ == '__main__':
    main()



