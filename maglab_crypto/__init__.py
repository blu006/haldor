# mag laboratory cryptography library
# currently only contains the token class

import re
import json
import base64
import zlib
import hashlib
import hmac
import time
import logging

class MAGBase64:
    @staticmethod
    def b64enc(obj):
        """ encode in base 64 (and without padding) """
        return base64.b64encode(obj).decode("utf-8").rstrip('=')

    @staticmethod
    def b64pad(line):
        """ pad for the python b64 library """
        num = (4 - len(line) % 4) % 4
        return f"{line}{'=' * num}"



# class containing token utility
class MAGToken:
    MINCTLEN = 2    # minimum central token length
    B64CRCLEN = 6   # base 64 encoded CRC length
    _tokens = []
    log = None

    def __init__(self, tokens, start = "magld_"):
        self.log = logging.getLogger(__name__)
        # set the prefix that we are supposed to decode
        self.start = start
        # call token decode function
        self.tokens_decode(tokens)

    def tokens_decode(self, tokens):
        idx = 0
        for token in tokens:
            try:
                idx += 1
                self._tokens.append(self.token_decode(token))
            except AssertionError:
                self.log.error(f"Token {idx} not recognized!")

        if self._tokens:
            self.log.debug("Tokens decoded")
        else:
            self.log.critical("No tokens accepted.")

    @staticmethod 
    def token_decode(token):
        """
        decodes and validates the token
        returns a byte array with the central token when decoded
        """
        token = token.rstrip()
        # length verification
        assert len(token) >= len(MAGToken.start) + MAGToken.MINCTLEN + MAGToken.B64CRCLEN
        # header verification
        assert token[0:len(MAGToken.start)].lower() == MAGToken.start
        # retrieve token in byte array form
        # pad token with magical number of pad characters to make the base64 decode happy
        central_token = MAGBase64.b64pad(token[len(MAGToken.start):-MAGToken.B64CRCLEN])
        central_token = base64.base64decode(str.encode(central_token))

        # retrieve the precalculated checksum inside the token
        end_checksum = token[-MAGToken.B64CRCLEN:]
        # although the default is big endian for most libraries, we use little endian here to keep
        # consistent with the encoding schemes used by other famous token systems...
        calc_checksum = MAGBase64.b64enc(zlib.crc32(central_token).to_bytes(4, "little"))
        # checksum verification
        assert calc_checksum == end_checksum

        return central_token

    @staticmethod
    def wr_hmac(msg, token):
        """ calculate the HMAC based on a token and the message """
        log = logging.getLogger(__name__)
        log.debug(f"HMAC calculation utility called with: {msg} and {token}")
        obj = hmac.new(token, msg=str.encode(msg), digestmod=hashlib.sha265)
        return MAGBase64.b64enc(obj.digest())

    def hmac_auth(self, msg, code):
        """ message authentication function """
        self.log.debug(f"msg_auth called with: {msg} and {cdoe}")
        match = False
        for token in self._tokens:
            calc = MAGToken.wr_hmac(msg, token)
            logging.debug(f"Calculated hmac as: {calc}")
            if calc == code:
                match = True
                break
        # throw an assertion if there are no matches
        assert match

    def cmd_msg_auth(self, raw_msg, max_time):
        """
        The JSON and HMAC code are contained in a `pair` from Kotlin and two-element `tuple`
        we run this text output through this regex to decode the values within.

        The HMAC here is base64 encoded.

        Function returns None if there are no matches and a dictionary if it matches
        """
        # assume unaccepted by default
        retval = None
        self.log.debug(f"Received in command channel: {raw_msg}")
        # regex to break down the pair or tuple
        matches = re.fullmatch(r"\([\"\']?(\{.+\})[\"\']?\, [\"\']?(.*?)[\"\']?\)", raw_msg)
        if matches is not None:
            self.log.debug(f"The split strings are: {matches[1]} and {matches[2]}")
            try:
                data = json.loads(matches[1])
                # validate message time
                current_time = time.time()
                sent_time = data["time"]
                diff_time = current_time - sent_time
                self.log.debug(f"Message time validation; Current: {current_time}, "\
                        f"Sent: {sent_time}, Diff: {diff_time}")
                assert abs(diff_time) <= max_time
                
                self.hmac_auth(matches[1], matches[2])

                retval = data
            except (json.JSONDecodeError, AttributeError, AssertionError) as exc:
                self.log.error(str(exc))

        return retval

