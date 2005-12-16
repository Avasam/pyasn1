# BER encoder
import string
from pyasn1.type import tag, univ, char, useful
from pyasn1.codec.ber import eoo
from pyasn1 import error

class Error(Exception): pass

class AbstractItemEncoder:
    supportIndefLenMode = 1
    def _encodeTag(self, t, isConstructed):
        if isConstructed:
            return chr(t[0]|t[1]|t[2]|tag.tagFormatConstructed)
        else:
            return chr(t[0]|t[1]|t[2])

    def _encodeLength(self, length, defMode):
        if not defMode and self.supportIndefLenMode:
            return '\x80'
        if length < 0x80:
            return chr(length)
        elif length < 0xFF:
            return '\x81%c' % length
        elif length < 0xFFFF:
            return '\x82%c%c' % (
                (length >> 8) & 0xFF, length & 0xFF
                )
        elif length < 0xFFFFFF:
            return '\x83%c%c%c' % (
                (length >> 16) & 0xFF,
                (length >> 8) & 0xFF,
                length & 0xFF
                )
        #...more octets may be added
        else:
            raise Error(
                'Too large length (%d)' % length
                )

    def _encodeValue(self, encodeFun, value, defMode, maxChunkSize):
        raise Error('Not implemented')

    def _encodeEndOfOctets(self, encodeFun, defMode):
        if defMode or not self.supportIndefLenMode:
            return ''
        else:
            return encodeFun(eoo.endOfOctets, defMode)
        
    def encode(self, encodeFun, value, defMode, maxChunkSize):
        substrate, isConstructed = self._encodeValue(
            encodeFun, value, defMode, maxChunkSize
            )
        tagSet = value.getTagSet()
        if tagSet:
            if not isConstructed:  # primitive form implies definite mode
                defMode = 1
            return self._encodeTag(
                tagSet[-1], isConstructed
                ) + self._encodeLength(
                len(substrate), defMode
                ) + substrate + self._encodeEndOfOctets(encodeFun, defMode)
        else:
            return substrate  # untagged value

class EndOfOctetsEncoder(AbstractItemEncoder):
    def _encodeValue(self, encodeFun, value, defMode, maxChunkSize):
        return '', 0

class ExplicitlyTaggedItemEncoder(AbstractItemEncoder):
    def _encodeValue(self, encodeFun, value, defMode, maxChunkSize):
        return encodeFun(value.clone(tagSet=value.getTagSet()[:-1]),
                         defMode, maxChunkSize), 1

explicitlyTaggedItemEncoder = ExplicitlyTaggedItemEncoder()

class IntegerEncoder(AbstractItemEncoder):
    supportIndefLenMode = 0
    def _encodeValue(self, encodeFun, value, defMode, maxChunkSize):
        octets = []
        value = long(value) # to save on ops on asn1 type
        while 1:
            octets.insert(0, value & 0xff)
            if value == 0 or value == -1:
                break
            value = value >> 8
        if value == 0 and octets[0] & 0x80:
            octets.insert(0, 0)
        while len(octets) > 1 and \
                  (octets[0] == 0 and octets[1] & 0x80 == 0 or \
                   octets[0] == 0xff and octets[1] & 0x80 != 0):
            del octets[0]
        return string.join(map(chr, octets), ''), 0

class BitStringEncoder(AbstractItemEncoder):
    def _encodeValue(self, encodeFun, value, defMode, maxChunkSize):
        if not maxChunkSize or len(value) <= maxChunkSize*8:
            r = {}; l = len(value); p = j = 0
            while p < l:
                i, j = divmod(p, 8)
                r[i] = r.get(i,0) | value[p]<<(7-j)
                p = p + 1
            keys = r.keys(); keys.sort()
            return chr(7-j) + string.join(
                map(lambda k,r=r: chr(r[k]), keys),''
                ), 0
        else:
            pos = 0; substrate = ''
            while 1:
                # count in octets
                v = value.clone(value=value[pos*8:pos*8+maxChunkSize*8])
                if not v:
                    break
                substrate = substrate + encodeFun(v, defMode, maxChunkSize)
                pos = pos + maxChunkSize
            return substrate, 1

class OctetStringEncoder(AbstractItemEncoder):
    def _encodeValue(self, encodeFun, value, defMode, maxChunkSize):
        if not maxChunkSize or len(value) <= maxChunkSize:
            return str(value), 0
        else:
            pos = 0; substrate = ''
            while 1:
                v = value.clone(value=value[pos:pos+maxChunkSize])
                if not v:
                    break
                substrate = substrate + encodeFun(v, defMode, maxChunkSize)
                pos = pos + maxChunkSize
            return substrate, 1

class NullEncoder(AbstractItemEncoder):
    supportIndefLenMode = 0
    def _encodeValue(self, encodeFun, value, defMode, maxChunkSize):
        return '', 0

class ObjectIdentifierEncoder(AbstractItemEncoder):
    supportIndefLenMode = 0
    def _encodeValue(self, encodeFun, value, defMode, maxChunkSize):    
        oid = tuple(value)
        if len(oid) < 2:
            raise error.PyAsn1Error('Short OID %s' % value)

        # Build the first twos
        index = 0
        subid = oid[index] * 40
        subid = subid + oid[index+1]
        if 0 > subid > 0xff:
            raise error.PyAsn1Error(
                'Initial sub-ID overflow %s in OID %s' % (oid[index:], value)
            )
        octets = [ chr(subid) ]
        index = index + 2

        # Cycle through subids
        for subid in oid[index:]:
            if subid > -1 and subid < 128:
                # Optimize for the common case
                octets.append(chr(subid & 0x7f))
            elif subid < 0 or subid > 0xFFFFFFFFL:
                raise error.PyAsn1Error(
                    'SubId overflow %s in %s' % (subid, value)
                    )
            else:
                # Pack large Sub-Object IDs
                res = [ chr(subid & 0x7f) ]
                subid = subid >> 7
                while subid > 0:
                    res.insert(0, chr(0x80 | (subid & 0x7f)))
                    subid = subid >> 7 
                # Convert packed Sub-Object ID to string and add packed
                # it to resulted Object ID
                octets.append(string.join(res, ''))
        return string.join(octets, ''), 0
    
class SequenceOfEncoder(AbstractItemEncoder):
    def _encodeValue(self, encodeFun, value, defMode, maxChunkSize):
        if hasattr(value, 'setDefaultComponents'):
            value.setDefaultComponents()
        value.verifySizeSpec()
        substrate = ''; idx = len(value)
        while idx > 0:
            idx = idx - 1
            if value[idx] is None:  # Optional component
                continue
            if hasattr(value, 'getDefaultComponentByPosition'):
                if value.getDefaultComponentByPosition(idx) == value[idx]:
                    continue
            substrate = encodeFun(
                value[idx], defMode, maxChunkSize
                ) + substrate
        return substrate, 1

codecMap = {
    eoo.endOfOctets.tagSet: EndOfOctetsEncoder(),
    univ.Boolean.tagSet: IntegerEncoder(),
    univ.Integer.tagSet: IntegerEncoder(),
    univ.BitString.tagSet: BitStringEncoder(),
    univ.OctetString.tagSet: OctetStringEncoder(),
    univ.Null.tagSet: NullEncoder(),
    univ.ObjectIdentifier.tagSet: ObjectIdentifierEncoder(),
    univ.Enumerated.tagSet: IntegerEncoder(),
    # Sequence & Set have same tags as SequenceOf & SetOf
    univ.SequenceOf.tagSet: SequenceOfEncoder(),
    univ.SetOf.tagSet: SequenceOfEncoder(),
    univ.Choice.tagSet: SequenceOfEncoder(),
    # character string types
    char.UTF8String.tagSet: OctetStringEncoder(),
    char.NumericString.tagSet: OctetStringEncoder(),
    char.PrintableString.tagSet: OctetStringEncoder(),
    char.TeletexString.tagSet: OctetStringEncoder(),
    char.VideotexString.tagSet: OctetStringEncoder(),
    char.IA5String.tagSet: OctetStringEncoder(),
    char.GraphicString.tagSet: OctetStringEncoder(),
    char.VisibleString.tagSet: OctetStringEncoder(),
    char.GeneralString.tagSet: OctetStringEncoder(),
    char.UniversalString.tagSet: OctetStringEncoder(),
    char.BMPString.tagSet: OctetStringEncoder(),
    # useful types
    useful.GeneralizedTime.tagSet: OctetStringEncoder(),
    useful.UTCTime.tagSet: OctetStringEncoder()        
    }

class Encoder:
    def __init__(self, codecMap):
        self.__codecMap = codecMap

    def __call__(self, value, defMode=1, maxChunkSize=0):
        tagSet = value.getTagSet()
        if len(tagSet) > 1:
            concreteEncoder = explicitlyTaggedItemEncoder
        else:
            concreteEncoder = self.__codecMap.get(tagSet)
            if not concreteEncoder:
                concreteEncoder = self.__codecMap.get(
                    tag.TagSet(tagSet.getBaseTag(), tagSet.getBaseTag()) # XXX
                    )
        if concreteEncoder:
            return concreteEncoder.encode(
                self, value, defMode, maxChunkSize
                )
        else:
            raise Error('No encoder for %s' % value)

encode = Encoder(codecMap)