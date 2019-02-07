#!/usr/bin/env python
"""
    Control your Somfy receivers with a Sonoff RF Bridge
    Generate RfRaw B0 commands from a sniffed B1 string

    Copyright 2019 Sebastian Haas <sehaas@deebas.com>
    https://github.com/sehaas/yfmos
    License: MIT

"""

import sys
import os
import argparse
from ConfigParser import SafeConfigParser
from enum import Enum, IntEnum

class ManchesterDecode:
    """
        Decoder based on https://github.com/altelch/Somfy-RTS
    """
    def init(self, nextBit, secondPulse):
        self.nextBit = nextBit
        self.secondPulse = secondPulse
        self.count = 0
        self.bitvec = ""

    def addShortPulse(self):
        if self.secondPulse:
            self.bitvec=self.bitvec+str(self.nextBit)
            self.count=self.count+1
            self.secondPulse=False
        else:
            self.secondPulse=True

    def addLongPulse(self):
        if not self.secondPulse:
            return False
        self.bitvec=self.bitvec+str(self.nextBit)
        self.nextBit = self.nextBit ^ 1
        self.count=self.count+1
        return True

    def get_bitvector(self):
        return int(self.bitvec,base=2)

class ManchesterEncode:
    def init(self, longPulse, shortPulse):
        self.longPulse = str(longPulse)
        self.shortPulse = str(shortPulse)
        self.encoded = ""

    def addData(self, bitvec):
        prev = bitvec[0]
        for i in range(1,len(bitvec)):
            if bitvec[i] == prev:
                self.encoded += (self.shortPulse * 2)
            else:
                self.encoded += self.longPulse
            prev = bitvec[i]

    def get_encoded(self):
        return self.encoded

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
    ST_UNKNOWN  = 0
    ST_HW_SYNC1 = 1
    ST_HW_SYNC2 = 2
    ST_HW_SYNC3 = 3
    ST_HW_SYNC4 = 4
    ST_SW_SYNC1 = 5
    ST_SW_SYNC2 = 6
    ST_PAYLOAD = 7
    ST_DONE = 8

class Yfmos(object):
    CONFIG_FILE='.yfmosrc'

    def __init__(self):
        parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''\
Available commands:
    init	Initalize the remote by with a B1 data string
    gen		Generate B0 data string
    run		Run command on Tasmota RF bridge
    print	Print payload data
''')
        parser.add_argument('command', help='Subcommand to run')
        args = parser.parse_args(sys.argv[1:2])
        if not hasattr(self, args.command):
            print 'Unrecognized command'
            parser.print_help()
            exit(1)
        getattr(self, args.command)()

    def init(self):
        parser = argparse.ArgumentParser(
        prog='%s init' % os.path.basename(sys.argv[0]),
        description='Initalize the remote by with a B1 data string')
        # TODO use --device to override saved device id
        parser.add_argument('--device', '-d', help='Remote Handheld Addr.', type=self.__auto_int)
        parser.add_argument('--profile', '-p', default='main')
        parser.add_argument('--rollingcode', '-r', type=int, default=-1)
        parser.add_argument(metavar='...', dest='b1string', help='B1 string', nargs=argparse.REMAINDER)
        args = parser.parse_args(sys.argv[2:])
        if len(args.b1string) == 0:
            parser.print_help()
            exit(1)

        options_debug = False
        listOfElem = args.b1string
        #print("ListofElem: %s" % listOfElem) #HK
        iNbrOfBuckets = int(listOfElem[2])
        #print("NumBuckets: %d" % iNbrOfBuckets) #HK
        # Start packing
        state = States.ST_UNKNOWN
        pulse = {}
        buckets = [None] * iNbrOfBuckets
        for i in range(0, iNbrOfBuckets):
            iValue = int(listOfElem[i + 3], 16)
            buckets[i] = iValue
            if iValue > 448 and iValue < 832:
                pulse[i]="Short"
            if iValue > 896 and iValue < 1664:
                pulse[i]="Long"
            if iValue > 1792 and iValue < 3328:
                pulse[i]="HWsync"
            if iValue > 3136 and iValue < 5824:
                pulse[i]="SWsync"
            if iValue > 25000:
                pulse[i]="InterFrameGap"
            if(options_debug):
                print("Bucket %d: %s (%d)" % (i, pulse[i], iValue)) #HK
        szDataStr = listOfElem[iNbrOfBuckets + 3]
        iLength = len(szDataStr)
        strNew = ""
        decode=ManchesterDecode()
        for i in range(0, iLength):
            pos = i
            strNew += szDataStr[pos:pos+1]
            strNew += " "
        if(options_debug):
            print("Data: %s" % (strNew))
        listOfElem = strNew.split()
        iNbrOfNibbles = len(listOfElem)
        for i in range(0,iNbrOfNibbles):
            if(options_debug):
                print pulse[int(listOfElem[i])]
            if pulse[int(listOfElem[i])] is "HWsync":
                #print "HWsync"
                state=state+1
                if state > States.ST_HW_SYNC4:
                    state=States.ST_UNKNOWN
            elif pulse[int(listOfElem[i])] is "SWsync" and state == States.ST_HW_SYNC4:
                #print "SWsync"
                state=States.ST_SW_SYNC2
            elif pulse[int(listOfElem[i])] is "Long" and state == States.ST_SW_SYNC2:
                #print "Sync Long",
                state=States.ST_PAYLOAD
                decode.init(1,True)
            elif pulse[int(listOfElem[i])] is "Short" and state == States.ST_SW_SYNC2:
                #print "Sync Short",
                state=States.ST_PAYLOAD
                decode.init(0,False)
            elif state == States.ST_PAYLOAD:
                if pulse[int(listOfElem[i])] is "Short":
                    #print "Short",
                    decode.addShortPulse()
                elif pulse[int(listOfElem[i])] is "Long":
                    #print "Long",
                    if not decode.addLongPulse():
                        state = States.ST_UNKNOWN
                elif pulse[int(listOfElem[i])] is "InterFrameGap":
                    #print "InterFrameGap"
                    if decode.count==55:
                        decode.bitvec=decode.bitvec+str(decode.nextBit)
                    state = States.ST_DONE
                else:
                    state = States.ST_UNKNOWN
            else:
                state=States.ST_UNKNOWN
            if(options_debug):
                print("%d: %s" % (i,States(state)))

        if(options_debug):
            print(bin(decode.get_bitvector()))
            print(hex(decode.get_bitvector()))
        number = decode.get_bitvector()
        frame = {}
        frame[0] = (number>>48) & 0xff
        frame[1] = (number>>40) & 0xff
        frame[2] = (number>>32) & 0xff
        frame[3] = (number>>24) & 0xff
        frame[4] = (number>>16) & 0xff
        frame[5] = (number>>8) & 0xff
        frame[6] = number & 0xff
        frame = self.__deobfuscate(frame)
        self.__printFrame(frame)

        config = SafeConfigParser()
        config.read(self.CONFIG_FILE)
        if not config.has_section(args.profile):
            config.add_section(args.profile)
        rcode = args.rollingcode
        if rcode < 0:
            rcode = frame[2]<<8 | frame[3]
        config.set(args.profile, "RollingCode", str(rcode))
        if args.device > 0:
                config.set(args.profile, "Device", "0x%06X" % (args.device))
        else:
                config.set(args.profile, "Device", "0x%02X%02X%02X" % (frame[4], frame[5], frame[6]))
        config.set(args.profile, "Buckets", ",".join(map(str,buckets)))
        for key in pulse:
            config.set(args.profile, pulse[key], str(key))
        with open(self.CONFIG_FILE, 'wb') as configfile:
            config.write(configfile)

    def gen(self):
        parser = argparse.ArgumentParser(
        prog='%s gen' % os.path.basename(sys.argv[0]),
        description='Generate B0 data string')
        parser.add_argument('command', type=Commands.from_string, choices=list(Commands))
        parser.add_argument('--repeat', '-r', type=int, default=1)
        parser.add_argument('--profile', '-p', default='main')
        args = parser.parse_args(sys.argv[2:])

        # TODO: check for existing CONFIG_FILE
        config = SafeConfigParser()
        config.read(self.CONFIG_FILE)
        buckets = map(int, config.get(args.profile, 'buckets').split(','))
        device = int(config.get(args.profile, 'Device'), 0)
        rollingCode = config.getint(args.profile, 'RollingCode') + 1

        payload = self.__gen_payload(args.command, rollingCode, device)
        payload = self.__calc_checksum(payload)
        self.__printFrame(payload)
        payload = self.__obfuscate(payload)
        bitvec = self.__to_bitvec(payload)

        encoder = ManchesterEncode()
        encoder.init(config.get(args.profile, 'Long'), config.get(args.profile, 'Short'))
        encoder.addData(bitvec)
        dataStr = encoder.get_encoded()
        # FIXME: generate HWSync/SWSync string
        tmpStr = "05 %02X %04X %04X %04X %04X %04X 0000000000000012%s34" % (args.repeat,
            buckets[0], buckets[1], buckets[2], buckets[3], buckets[4], dataStr)
        strLen = int(len(tmpStr.replace(' ','')) / 2)
        print("RfRaw AA B0 %02X %s 55" % (strLen, tmpStr))

        config.set(args.profile, 'RollingCode', str(rollingCode))
        with open(self.CONFIG_FILE, 'wb') as configfile:
            config.write(configfile)

    def run(self):
        print("TBD - post B0 directly to Tasmota device")

    def __auto_int(self, x):
        return int(x, 0)

    def __gen_payload(self, cmd, code, device):
        payload = {}
        payload[0] = 0xA1
        # Command
        payload[1] = cmd.value & 0xF0
        # Rollingcode
        payload[2] = (code>>8) & 0xFF
        payload[3] = code & 0xFF
        # device ID
        payload[4] = (device>>16) & 0xFF
        payload[5] = (device>>8) & 0xFF
        payload[6] = device & 0xFF
        return payload

    def __calc_checksum(self, data):
        checksum = 0
        for i in range(len(data)):
            checksum = checksum ^ data[i] ^ (data[i] >> 4)
        data[1] |= (checksum & 0x0F)
        return data

    def __obfuscate(self, data):
        for i in range(1,len(data)):
            data[i] = data[i] ^ data[i-1]
        return data

    def __deobfuscate(self, data):
        for i in range(len(data)-1, 1, -1):
            data[i] = data[i] ^ data[i-1]
        return data

    def __to_bitvec(self, data):
        out = (data[0]<<48 | data[1]<<40 | data[2] << 32 | data[3] << 24
            | data[4] << 16 | data[5] << 8 | data[6])
        return bin(out)[2:]

    def __printFrame(self, frame):
        print("Group       A       B       C       D       F               G                    ")
        print("Byte:       0H      0L      1H      1L      2       3       4       6       7    ")
        print("        +-------+-------+-------+-------+-------+-------+-------+-------+-------+")
        print("        !  0xA  + R-KEY ! C M D + C K S !  Rollingcode  ! Remote Handheld Addr. !")
        print("        !  0x%01X  +  0x%01X  !  0x%01X  +  0x%01X  !    0x%04X     !       0x%06X        !" % (
            (frame[0]>>4) & 0xF, frame[0] & 0xF, (frame[1]>>4) & 0xF, frame[1] & 0xF,
            (frame[2]<<8) + frame[3], (frame[4]<<16) + (frame[5]<<8) + frame[6]))
        print("        +-------+-------+-------+-------+MSB----+----LSB+LSB----+-------+----MSB+")


if __name__ == '__main__':
    Yfmos()
