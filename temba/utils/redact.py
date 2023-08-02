import json
import urllib.parse
import xml.sax.saxutils
from collections import OrderedDict
from urllib.parse import parse_qs, urlencode

HTTP_BODY_BOUNDARY = "\r\n\r\n"

# variations of the needle being redacted will be trimmed to this number of chars
TRIM_NEEDLE_TO = 7

VARIATION_ENCODERS = (
    lambda s: s,  # original
    urllib.parse.quote,  # URL with spaces as %20
    urllib.parse.quote_plus,  # URL with spaces as +
    xml.sax.saxutils.escape,  # XML/HTML reserved chars
    lambda s: json.dumps(s)[1:-1],  # JSON reserved chars
)

HTTP_BODY_FORMATS = (
    (json.loads, json.dumps),
    (lambda s: parse_qs(s, strict_parsing=True), lambda d: urlencode(d, doseq=True, safe="*")),
)


def text(s, needle, mask):
    """
    Redacts a value from the given text by replacing it with a mask.

    Variations of the value are generated, e.g. 252615518585 becomes 0252615518585 +252615518585 etc and if these are
    found in the text they are redacted. Variations include different encodings as well, e.g. %2B252615518585

    Contact identifying information is more volatile at the start of the value than the end. In this case contact
    identity is masked regardless of a different prefix: 0615518585 -> 0******** for +252615518585
    """

    assert isinstance(s, str) and isinstance(needle, str) and isinstance(mask, str)

    for variation in _variations(needle):
        s = s.replace(variation, mask)

    return s


def replace_headers(raw_data, headers_to_replace):
    try:
        headers = OrderedDict()
        raw_headers, raw_body = raw_data.split(HTTP_BODY_BOUNDARY)
        split_headers = str(raw_headers).split("\r\n")

        # parse headers
        for line in split_headers[1:]:
            key, value = line.split(": ")
            headers[key] = value

        # replace needed
        for key, value in headers_to_replace.items():
            if key in headers:
                headers[key] = value

        # rebuild http message
        first_header = split_headers[0].strip()
        raw_headers = "\r\n".join([first_header] + [": ".join((key, value)) for key, value in headers.items()])
        raw_message = HTTP_BODY_BOUNDARY.join((raw_headers, raw_body))
        return raw_message
    except (KeyError, ValueError):
        return raw_data


def http_trace(trace, needle, mask, body_keys=()):
    """
    Redacts a value from the given HTTP trace by replacing it with a mask. If body_keys is specified then we also try
    to parse the body and replace those keyed values with the mask. Bodies are parsed as JSON and URL encoded.
    """

    trace = replace_headers(trace, {"Authorization": "*" * 8})

    *rest, body = trace.split(HTTP_BODY_BOUNDARY)

    if body and body_keys:
        parsed = False
        for decoder, encoder in HTTP_BODY_FORMATS:
            try:
                decoded_body = decoder(body)
                decoded_body = _recursive_replace(decoded_body, body_keys, mask)

                body = encoder(decoded_body)
                parsed = True
                break
            except ValueError:
                continue

        # if body couldn't be parsed.. masked entire body
        if not parsed:
            body = mask

    # reconstruct the trace
    rest.append(body)
    body = HTTP_BODY_BOUNDARY.join(rest)

    # finally do a regular text-level redaction of the value
    return text(body, needle, mask)


def _recursive_replace(obj, keys, mask):
    """
    Recursively looks for specified keys in structure of dicts and lists and replaces their values with mask if found
    """

    if isinstance(obj, dict):
        tmp = {}
        for k, v in obj.items():
            if k in keys:
                tmp[k] = mask
            else:
                tmp[k] = _recursive_replace(v, keys, mask)

        return tmp

    elif isinstance(obj, list):
        return [_recursive_replace(v, keys, mask) for v in obj]

    else:
        return obj


def _variations(needle):
    """
    Generates variations based on a given base value
    """

    bases = {needle}

    # include variations with 0 and + prepended, and replaced with the other
    if needle.startswith("0"):
        bases.add("+" + needle[1:])
    elif not needle.startswith("+"):
        bases.add("0" + needle)

    if needle.startswith("+"):
        bases.add("0" + needle[1:])
    elif not needle.startswith("0"):
        bases.add("+" + needle)

    trimmed = needle[1:]
    while len(trimmed) >= TRIM_NEEDLE_TO:
        bases.add(trimmed)
        trimmed = trimmed[1:]

    # for each base variation, generate new variations using different encodings
    variations = set()
    for b in bases:
        for encoder in VARIATION_ENCODERS:
            variations.add(encoder(b))

    # return in order of longest to shortest, a-z
    return sorted(variations, key=lambda x: (len(x), x), reverse=True)
