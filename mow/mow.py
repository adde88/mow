import struct
import socket

BIG_ENDIAN = '>L'
LITTLE_ENDIAN = '<L'
POST = b'POST'
GET = b'GET'


def _bc(string):
    """
    Convert strings to bytes if they are not already. Functions should call
    this where needed.

    :param string: String to convert, or not.
    :type string: str

    :return: Bytes representation of the string.
    :rtype: bytes
    """
    if not isinstance(string, bytes):
        return bytes(string, 'utf8')
    return string


class Overflow:
    """
    Class to generate overflow strings for MIPS targets. Upon initialization
    this class will dynamically create class variables to represent the 's'
    registers. They are initialized to 'AAAA' through 'IIII' based on their
    position and can be changed after class initialization is complete.
    """
    def __init__(self, register_dist, register_count, endianess,
                 padding_after_ra=0, gadgets_base=0,
                 overflow_string_contents='', bad_bytes=None):
        """
        Initialize the overflow class.

        :param register_dist: Distance from the buffer containing the
        overflow to the s0 register in memory.
        :type register_dist: int

        :param register_count: Number of registers saved in the function. If
        the only register is s0, then provide 1. If s0 - s7 and fp are available
        then provide 9.
        :type register_count: int

        :param endianess: Either mow.BIG_ENDIAN or mow.LITTLE_ENDIAN.
        :type endianess: str

        :param padding_after_ra: Indicate amount of padding after ra on the
        stack.
        :type padding_after_ra: int

        :param gadgets_base: Base address of ROP gadgets used in a loaded
        library. If multiple base addresses are needed set this value to 0 and
        perform the math when setting the register values.
        :type gadgets_base: int

        :param overflow_string_contents: If the target buffer contains a
        string prior to the overwrite, it can be entered here. This value is
        only used to compute length values.
        :type overflow_string_contents: str

        :param bad_bytes: List of invalid bytes that cannot be sent to the
        target.
        :type bad_bytes: list(int)
        """
        if register_count < 0 or register_count > 9:
            raise Exception('Register count must be between 0 and 9.')

        if endianess not in [BIG_ENDIAN, LITTLE_ENDIAN]:
            raise Exception('Invalid value for endianess. Must be '
                            'mow.LITTLE_ENDIAN or mow.BIG_ENDIAN')

        self._register_dist = register_dist
        self._padding_after_ra = padding_after_ra
        self._register_count = register_count
        self._endianess = endianess
        self._gadget_base = gadgets_base
        self._overflow_string_contents = overflow_string_contents
        self.ra = b'JJJJ'
        self._stack_write = b''
        self._bad_bytes = [bytes([curr_byte]) for curr_byte in bad_bytes] \
            if bad_bytes is not None else None

        # Dynamically generate register class variables.
        for index in range(0, register_count):
            if index == 8:
                register_name = 'fp'
            else:
                register_name = 's%d' % index
            register_value = bytes(chr(ord('A') + index) * 4, 'utf8')
            setattr(self, register_name, register_value)

    def _pack_register(self, register):
        """
        Pack a register based on the endianess of the target.

        :param register: Register value to pack.
        :type register: bytes or int.

        :return: Packed register value.
        :rtype: bytes
        """
        if not isinstance(register, bytes) and not isinstance(register, int):
            raise Exception('Register must be bytes or int type.')

        if isinstance(register, bytes):
            print(register.hex())
            if self._has_bad_bytes(register):
                raise Exception('Bad byte found.')
            return register

        register_value = struct.pack(self._endianess, register +
                                     self._gadget_base)
        print('%s (0x%04x + 0x%04x)' % (register_value.hex(),
                                        self._gadget_base, register))
        if self._has_bad_bytes(register_value):
            raise Exception('Bad byte found.')
        return register_value

    def _is_safe_write(self, location, length):
        """
        Validate it is safe to write at the position in the stack. Just a
        safety validation to check for a potentially bad ROP chain.

        :param location: Location in the stack to write.
        :type location: int

        :param length: Length of the proposed write.
        :type length: int

        :return: True if safe, False if the location contains data.
        :rtype: bool
        """
        if location < 0:
            raise Exception('Cannot write to negative location.')

        if length < 0:
            raise Exception('Cannot write a negative length.')

        if len(self._stack_write) < location:
            return True

        current_contents = self._stack_write[location:location + length]
        if current_contents == b'X' * len(current_contents):
            return True
        return False

    def add_to_stack(self, padding, address=None, command=None,
                     force_overwrite=False):
        """
        Write an address or command on the stack passed the $ra address. Used
        for writing ROP gadget addresses and commands on the stack.

        :param padding: Padding to place before entry on the stack.
        :type padding: int

        :param address: Address to write at the provided offset. Gadget base
        is added to this value prior to the write.
        :type address: int

        :param command: Command to write at the provided offset.
        :type command: str

        :param force_overwrite: Force overwriting values on the stack,
        preventing an exception from being thrown.
        :type force_overwrite: bool

        :raises: Exception if invalid parameters are provided.
        :raises: Exception if a write collision occurs.
        """
        if not address and not command:
            raise Exception('Must provide address or command.')

        if address and command:
            raise Exception('Address and command cannot be written at the '
                            'same location on the stack.')
        
        if len(self._stack_write) < padding:
            self._stack_write += b'X' * (padding - len(self._stack_write))

        if address:
            if not isinstance(address, int):
                raise Exception('Address must be an integer.')

            if force_overwrite or self._is_safe_write(padding, 4):
                self._stack_write = self._stack_write[:padding] + \
                                    self._pack_register(address) + \
                                    self._stack_write[padding + 4:]
            else:
                raise Exception('Address write overwrote values on the stack.')

        elif command:
            if not isinstance(command, str):
                raise Exception('Command must be a string.')

            if force_overwrite or self._is_safe_write(padding, len(command)):
                self._stack_write = self._stack_write[:padding] + \
                                    _bc(command) + \
                                    self._stack_write[padding + len(command):]
            else:
                raise Exception('Command write overwrote values on the stack.')

    def _has_bad_bytes(self, byte_str):
        """
        Check for user defined bad bytes in the provided byte string.

        :param byte_str: Byte string to check for bad bytes.
        :type byte_str: bytes

        :return: False if no bad bytes are found, True if bad bytes are found.
        :rtype: bool
        """
        if not isinstance(byte_str, bytes):
            raise Exception('byte_str must be of type bytes.')

        if self._bad_bytes is None:
            return False

        for byte in self._bad_bytes:
            if byte in byte_str:
                print('Bad byte 0x%s found.' % byte.hex())
                return True
        return False

    def generate(self):
        """
        Generate an overflow string based on registers, $ra, and the stack.

        :return: Overflow string.
        :rtype: bytes
        """
        print('*' * 20)
        print('Overflow Generation')
        print('*' * 20)

        overflow = b'X' * (self._register_dist - len(
            self._overflow_string_contents))

        print('Bytes to first register: 0x%04x(%d) accounting for %d bytes in '
              'the string: %s' % (len(overflow), len(overflow),
                                  len(self._overflow_string_contents),
                                  self._overflow_string_contents))

        for index in range(0, self._register_count):
            if index == 8:
                register_value = getattr(self, 'fp')
                print('fp = 0x', end='')
            else:
                register_value = getattr(self, 's%d' % index)
                print('s%d = 0x' % index, end='')
            overflow += self._pack_register(register_value)

        print('ra = 0x', end=''),
        overflow += self._pack_register(self.ra)

        print('Adding %d bytes of padding after ra' % self._padding_after_ra)
        overflow += b'X' * self._padding_after_ra

        print('stack = %s' % self._stack_write)
        print('*' * 20)
        print()
        if self._has_bad_bytes(self._stack_write):
            raise Exception('Bad bytes found in the stack.')

        return overflow + self._stack_write


class SimpleRequest:
    """
    Generate simple request to use with urllib or requests library.
    """
    def __init__(self, host, port=80, request=None, args=None):
        """

        :param host: IP or host name of target.
        :type host: str

        :param port: Listening port on the target.
        :type port: int

        :param request: Page to request. Should not start with '/'.
        :type request: str

        :param args: Arguments to provide with request.
        :type args: dict
        """
        if not isinstance(host, str):
            raise Exception('Host must be a string.')

        if not isinstance(port, int):
            raise Exception('Port must be an integer.')

        if request and not isinstance(request, str):
            raise Exception('Request must be a string.')

        if args and not isinstance(args, dict):
            raise Exception('Args must be provided as a dictionary.')

        self.host = host
        self.port = port
        self.request = request
        self.args = args

    def create_url(self):
        url = 'http://%s' % self.host
        if self.port is not 80:
            url += ':%d' % self.port

        if self.request:
            url += '/%s' % self.request

        if self.args:
            url += '?'
            url += '&'.join('%s=%s' % (arg, self.args[arg]) for arg in
                            self.args)
        return url


class CustomRequest:
    """
    Generate a custom request. Use when you need to control header values.
    """
    def __init__(self, host, port, request_type, request_dest, headers,
                 data=None):
        """

        :param host: IP or host name of target.
        :type host: str

        :param port: Listening port on the target.
        :type port: int

        :param request_type: GET or POST request.
        :type request_type: str

        :param request_dest: Page to request.
        :type request_dest: str

        :param headers: Values to send in the header field.
        :type headers: dict

        :param data: Data to send with the packet.
        :type data: str or None
        """
        if not isinstance(host, str):
            raise Exception('Host must be a string.')

        if not isinstance(port, int):
            raise Exception('Port must be an integer')

        if request_type != b'POST' and request_type != b'GET':
            raise Exception('Request type must be mow.POST or mow.GET.')

        if not isinstance(request_dest, str):
            raise Exception('Request destination must be a string.')

        if not isinstance(headers, dict):
            raise Exception('Headers must be a dictionary.')

        if data and not isinstance(data, str):
            raise Exception('Data must be a string.')

        self.host = b'Host: %s:%d' % (_bc(host), port)

        if request_dest.startswith('/'):
            request_dest = request_dest[1:]

        self.request = b'%s /%s HTTP/1.1' % (request_type, _bc(request_dest))
        self.headers = headers
        self.data = _bc(data) if data is not None else b''

    def create_packet(self):
        """
        Create a packet based on provided values.

        :return: Generated packet.
        :rtype: bytes
        """
        print('*' * 20)
        print('Packet Generation')
        print('*' * 20)
        packet = self.request + b'\r\n'
        packet += self.host + b'\r\n'
        for header in self.headers:
            packet += b'%s: %s\r\n' % (_bc(header), _bc(self.headers[header]))

        data_len = len(self.data) if self.data is not None else 0
        packet += b'Content-Length: %d\r\n\r\n' % data_len
        packet += self.data

        print(packet.decode('utf8', 'ignore'))
        print('*' * 20)
        print()
        return packet


def send_packet(host, port, packet):
    """
    Send a packet to a target.

    :param host: IP or host name of the target.
    :type host: str

    :param port: Listening port on the target.
    :type port: int

    :param packet: Packet to send to the target. Generated from a
    CustomRequest class.
    :type packet: bytes
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, port))
    sock.send(packet)