#!/usr/bin/env python
'''
    Control your Somfy receivers with a Sonoff RF Bridge
    Generate RfRaw B0 commands from a sniffed B1 string

    Copyright 2019 Sebastian Haas <sehaas@deebas.com>
    https://github.com/sehaas/yfmos
    License: MIT

'''

from __future__ import print_function
import sys
import argparse
import pycurl
import logging
import time
from functools import partial
from ConfigParser import SafeConfigParser, NoOptionError
from enum import Enum, IntEnum
from io import BytesIO
from os.path import expanduser, basename
from recordtype import recordtype

logging.basicConfig(level=logging.DEBUG, filename='yfmos.log',
                    format='[%(asctime)s][%(levelname)s]'
                           '[%(filename)s:%(lineno)s - %(funcName)s()] '
                           '%(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')


class ManchesterDecode:
    '''
        Decoder based on https://github.com/altelch/Somfy-RTS
    '''
    def init(self, nextBit, secondPulse):
        self.nextBit = nextBit
        self.secondPulse = secondPulse
        self.count = 0
        self.bitvec = ''
        print('(init) next: %d, second: %s' % (nextBit, secondPulse))

    def addShortPulse(self):
        if self.secondPulse:
            self.bitvec = self.bitvec + str(self.nextBit)
            self.count = self.count + 1
            self.secondPulse = False
        else:
            self.secondPulse = True

    def addLongPulse(self):
        if not self.secondPulse:
            return False
        self.bitvec = self.bitvec + str(self.nextBit)
        self.nextBit = self.nextBit ^ 1
        self.count = self.count + 1
        return True

    def get_bitvector(self):
        return int(self.bitvec, base=2)


class ManchesterEncode:
    def init(self, longPulse, shortPulse):
        self.longPulse = str(longPulse)
        self.shortPulse = str(shortPulse)
        self.encoded = ''

    def addData(self, bitvec):
        prev = bitvec[0]
        for i in range(1, len(bitvec)):
            if bitvec[i] == prev:
                self.encoded += (self.shortPulse * 2)
            else:
                self.encoded += self.longPulse
            prev = bitvec[i]

    def get_encoded(self):
        return self.encoded + '3'


class Commands(Enum):
    MY = 0x10
    UP = 0x20
    DOWN = 0x40
    PROG = 0x80

    def __str__(self):
        return self.name

    @staticmethod
    def from_string(s):
        try:
            return Commands[s]
        except KeyError:
            raise ValueError()


class States(IntEnum):
    ST_UNKNOWN = 0
    ST_HW_SYNC1 = 1
    ST_HW_SYNC2 = 2
    ST_HW_SYNC3 = 3
    ST_HW_SYNC4 = 4
    ST_SW_SYNC1 = 5
    ST_SW_SYNC2 = 6
    ST_PAYLOAD = 7
    ST_DONE = 8


YfmosConfig = recordtype('YfmosConfig',
                         ['buckets', 'pulse', 'rollingCode', 'device'])


class Yfmos(object):
    CONFIG_FILE = expanduser('~/.yfmosrc')

    def __init__(self):
        self.debug = False
        parser = argparse.ArgumentParser(
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog='''\
Available commands:
    init	Initalize the remote by with a B1 data string
    gen		Generate B0 data string
    run		Run command on Tasmota Sonoff RF bridge
''')
        parser.add_argument('command', help='Subcommand to run')
        args = parser.parse_args(sys.argv[1:2])
        if not hasattr(self, args.command):
            print('Unrecognized command', file=sys.stderr)
            parser.print_help()
            exit(1)
        getattr(self, args.command)()

    def init(self):
        parser = argparse.ArgumentParser(
            prog='%s init' % basename(sys.argv[0]),
            description='Initalize a new remote')
        parser.add_argument('--profile', '-p', required=True,
                            help='Profile name. eg. "Window1"')
        parser.add_argument('--device', '-d', help='Remote Handheld Addr.',
                            type=self.__auto_int)
        parser.add_argument('--host', '-H', help='Tasmota RF Bridge URL')
        parser.add_argument('--rollingcode', '-r', type=int,
                            help='Startvalue of the rollingcode')
        parser.add_argument('--buckets', '-b', type=self.__auto_int, nargs=5,
                            metavar=('HWSync', 'SWSync', 'LongPulse',
                                     'ShortPulse', 'InterFrameGap'),
                            help='Bucket timings')
        parser.add_argument('--debug', action='store_true', required=False)
        parser.add_argument(metavar='...', dest='b1string', help='B1 string',
                            nargs=argparse.REMAINDER)
        args = parser.parse_args(sys.argv[2:])
        self.debug = args.debug

        parsedConfig = None
        if len(args.b1string) > 0:
            parsedConfig = self.__parge_B1(args.b1string)
        else:
            # default values
            parsedConfig = YfmosConfig(rollingCode=0, device=0xC0FFEE,
                                       pulse={0: 'HWsync', 1: 'SWsync',
                                              2: 'Long', 3: 'Short',
                                              4: 'InterFrameGap'},
                                       buckets=[0x9E2, 0x12CA, 0x4F6, 0x28A,
                                                0x6AE0])

        if args.rollingcode is not None:
            parsedConfig.rollingCode = args.rollingcode
        if args.buckets is not None:
            parsedConfig.buckets = args.buckets
        if args.device is not None:
            parsedConfig.device = args.device

        config = SafeConfigParser()
        config.read(self.CONFIG_FILE)
        if not config.has_section(args.profile):
            config.add_section(args.profile)
        config.set(args.profile, 'RollingCode', str(parsedConfig.rollingCode))
        config.set(args.profile, 'Device', '0x%06X' % (parsedConfig.device))
        config.set(args.profile, 'Buckets', ','.join(
                   map(str, parsedConfig.buckets)))
        if args.host is not None:
            config.set(args.profile, 'Host', args.host)
        for key in parsedConfig.pulse:
            config.set(args.profile, parsedConfig.pulse[key], str(key))
        with open(self.CONFIG_FILE, 'wb') as configfile:
            config.write(configfile)

    def gen(self):
        parser = argparse.ArgumentParser(
            prog='%s gen' % basename(sys.argv[0]),
            description='Generate B0 data string')
        parser.add_argument('--command', '-c', type=Commands.from_string,
                            choices=list(Commands), required=True)
        parser.add_argument('--repeat', '-r', type=int, default=1)
        parser.add_argument('--profile', '-p', default='main')
        args = parser.parse_args(sys.argv[2:])

        try:
            self.__gen_B0(args.command, args.repeat, args.profile,
                          self.__print_B0)
        except StandardError as e:
            print(e, file=sys.stderr)
            exit(1)

    def run(self):
        parser = argparse.ArgumentParser(
            prog='%s run' % basename(sys.argv[0]),
            description='Execute B0 data string')
        parser.add_argument('--command', '-c', type=Commands.from_string,
                            choices=list(Commands), required=True)
        parser.add_argument('--repeat', '-r', type=int, default=1)
        parser.add_argument('--profile', '-p', default='main')
        parser.add_argument('--host', '-H')
        args = parser.parse_args(sys.argv[2:])

        exec_B0 = partial(self.__exec_B0, args.host)
        try:
            self.__gen_B0(args.command, args.repeat, args.profile, exec_B0)
        except StandardError as e:
            print(e, file=sys.stderr)
            exit(1)

    def __parge_B1(self, b1String):
        listOfElem = b1String
        iNbrOfBuckets = int(listOfElem[2])
        if self.debug:
            print('ListofElem: %s' % listOfElem)
            print('NumBuckets: %d' % iNbrOfBuckets)

        state = States.ST_UNKNOWN
        pulse = {}
        buckets = [None] * iNbrOfBuckets
        for i in range(0, iNbrOfBuckets):
            iValue = int(listOfElem[i+3], 16)
            buckets[i] = iValue
            if iValue > 448 and iValue < 832:
                pulse[i] = 'Short'
            if iValue > 896 and iValue < 1664:
                pulse[i] = 'Long'
            if iValue > 1792 and iValue < 3328:
                pulse[i] = 'HWsync'
            if iValue > 3136 and iValue < 5824:
                pulse[i] = 'SWsync'
            if iValue > 25000:
                pulse[i] = 'InterFrameGap'
            if self.debug:
                print('Bucket %d: %s (%d)' % (i, pulse[i], iValue))
        szDataStr = listOfElem[iNbrOfBuckets+3]
        iLength = len(szDataStr)
        strNew = ''
        decode = ManchesterDecode()
        for i in range(0, iLength):
            pos = i
            strNew += szDataStr[pos:pos+1]
            strNew += ' '
        if self.debug:
            print('Data: %s' % (strNew))
        listOfElem = strNew.split()
        iNbrOfNibbles = len(listOfElem)
        for i in range(0, iNbrOfNibbles):
            if self.debug:
                print(pulse[int(listOfElem[i])])
            if pulse[int(listOfElem[i])] is 'HWsync':
                state = state + 1
                if state > States.ST_HW_SYNC4:
                    state = States.ST_UNKNOWN
            elif (pulse[int(listOfElem[i])] is 'SWsync' and
                    state == States.ST_HW_SYNC4):
                state = States.ST_SW_SYNC2
            elif (pulse[int(listOfElem[i])] is 'Long' and
                    state == States.ST_SW_SYNC2):
                state = States.ST_PAYLOAD
                decode.init(1, True)
            elif (pulse[int(listOfElem[i])] is 'Short' and
                    state == States.ST_SW_SYNC2):
                state = States.ST_PAYLOAD
                decode.init(0, False)
            elif state == States.ST_PAYLOAD:
                if pulse[int(listOfElem[i])] is 'Short':
                    decode.addShortPulse()
                elif pulse[int(listOfElem[i])] is 'Long':
                    if not decode.addLongPulse():
                        state = States.ST_UNKNOWN
                elif pulse[int(listOfElem[i])] is 'InterFrameGap':
                    if decode.count == 55:
                        decode.bitvec = decode.bitvec + str(decode.nextBit)
                    state = States.ST_DONE
                else:
                    state = States.ST_UNKNOWN
            else:
                state = States.ST_UNKNOWN
            if self.debug:
                print('%d: %s' % (i, States(state)))

        if self.debug:
            print(bin(decode.get_bitvector()))
            print(hex(decode.get_bitvector()))
        number = decode.get_bitvector()
        frame = {}
        frame[0] = (number >> 48) & 0xff
        frame[1] = (number >> 40) & 0xff
        frame[2] = (number >> 32) & 0xff
        frame[3] = (number >> 24) & 0xff
        frame[4] = (number >> 16) & 0xff
        frame[5] = (number >> 8) & 0xff
        frame[6] = number & 0xff
        frame = self.__deobfuscate(frame)

        rollingCode = frame[2] << 8 | frame[3]
        device = frame[4] << 16 | frame[5] << 8 | frame[6]
        if self.debug:
            self.__printFrame(frame)
        return YfmosConfig(device=device, buckets=buckets, pulse=pulse,
                           rollingCode=rollingCode)

    def __gen_B0(self, command, repeat, profile, callback):
        # TODO: check for existing CONFIG_FILE
        config = SafeConfigParser()
        config.read(self.CONFIG_FILE)
        if not config.has_section(profile):
            raise NameError('Profile "%s" not found' % profile)
        buckets = map(int, config.get(profile, 'buckets').split(','))
        device = int(config.get(profile, 'Device'), 0)
        rollingCode = config.getint(profile, 'RollingCode') + 1
        longPulse = config.get(profile, 'Long')
        shortPulse = config.get(profile, 'Short')
        hwSync = config.get(profile, 'HWSync')
        swSync = config.get(profile, 'SWSync')

        payload = self.__gen_payload(command, rollingCode, device)
        payload = self.__calc_checksum(payload)
        self.__printFrame(payload)
        payload = self.__obfuscate(payload)
        bitvec = self.__to_bitvec(payload)

        encoder = ManchesterEncode()
        encoder.init(longPulse, shortPulse)
        encoder.addData(bitvec)
        dataStr = encoder.get_encoded()
        # FIXME: generate HWSync/SWSync string
        tmpStr = '05 %02X %04X %04X %04X %04X %04X %s%s%s%s4' % (
            repeat, buckets[0], buckets[1], buckets[2], buckets[3], buckets[4],
            hwSync * 14, swSync, longPulse, dataStr)
        strLen = int(len(tmpStr.replace(' ', '')) / 2)

        b0String = 'RfRaw AA B0 %02X %s 55' % (strLen, tmpStr)
        if self.debug:
            logging.debug(b0String)
        callback(b0String, config, profile)

        config.set(profile, 'RollingCode', str(rollingCode))
        with open(self.CONFIG_FILE, 'wb') as configfile:
            config.write(configfile)

    def __print_B0(self, b0, config, profile):
        print(b0)

    def __exec_B0(self, hostArg, b0, config, profile):
        host = None
        try:
            host = config.get(profile, 'Host')
        except NoOptionError:
            pass
        if hostArg is not None:
            host = hostArg
        if hostArg is None:
            raise NameError('Host not set')

        buffer = BytesIO()
        c = pycurl.Curl()
        url = '%s/ax?c1=%s' % (host, b0.replace(' ', '%20'))
        c.setopt(c.URL, url)
        c.setopt(c.WRITEDATA, buffer)
        c.perform()
        status = c.getinfo(pycurl.HTTP_CODE)
        if status != 200:
            raise RuntimeError('Error calling "%s": %d' % (host, status))
        c.close()
        body = buffer.getvalue()

    def __auto_int(self, x):
        return int(x, 0)

    def __gen_payload(self, cmd, code, device):
        payload = {}
        payload[0] = 0xA1
        # Command
        payload[1] = cmd.value & 0xF0
        # Rollingcode
        payload[2] = (code >> 8) & 0xFF
        payload[3] = code & 0xFF
        # device ID
        payload[4] = (device >> 16) & 0xFF
        payload[5] = (device >> 8) & 0xFF
        payload[6] = device & 0xFF
        return payload

    def __calc_checksum(self, data):
        checksum = 0
        for i in range(len(data)):
            checksum = checksum ^ data[i] ^ (data[i] >> 4)
        data[1] |= checksum & 0x0F
        return data

    def __obfuscate(self, data):
        for i in range(1, len(data)):
            data[i] = data[i] ^ data[i-1]
        return data

    def __deobfuscate(self, data):
        for i in range(len(data)-1, 1, -1):
            data[i] = data[i] ^ data[i-1]
        return data

    def __to_bitvec(self, data):
        out = ((data[0] << 48) | (data[1] << 40) | (data[2] << 32) |
               (data[3] << 24) | (data[4] << 16) | (data[5] << 8) | (data[6]))
        return bin(out)[2:]

    def __printFrame(self, frame):
        print('Group       A       B       C       D       F               G                    ')
        print('Byte:       0H      0L      1H      1L      2       3       4       5       6    ')
        print('        +-------+-------+-------+-------+-------+-------+-------+-------+-------+')
        print('        !  0xA  + R-KEY ! C M D + C K S !  Rollingcode  ! Remote Handheld Addr. !')
        print('        !  0x%01X  +  0x%01X  !  0x%01X  +  0x%01X  !    0x%04X     !       0x%06X        !' % (
              (frame[0] >> 4) & 0xF, frame[0] & 0xF, (frame[1] >> 4) & 0xF, frame[1] & 0xF,
              (frame[2] << 8) + frame[3], (frame[4] << 16) + (frame[5] << 8) + frame[6]))
        print('        +-------+-------+-------+-------+MSB----+----LSB+LSB----+-------+----MSB+')


if __name__ == '__main__':
    Yfmos()
