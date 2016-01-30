#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Parse htmls obtained from dante_fetch_url.py"""

import argparse
import codecs
import datetime
import json
import logging
import os
import sys
import time

from HTMLParser import HTMLParser


class DanteSense(object):
    def __init__(self):
        self.word = ""
        self.sens_num = 0
        self.meaning = ""
        self.pos = ""
        self.examples = []

    def tostr(self):
        res = [
            "\n".join([
                u"sens num:\t{s.sens_num}",
                u"pos:\t{s.pos}",
                u"meaning:\t{s.meaning}",
            ]).format(s=self),
        ]
        for ex in self.examples:
            res.append("ex:\t{}".format(ex))

        return "\n".join(res)

    def to_json_d(self):
        res = {}
        res["name"] = u"{s.word}_{s.pos}_{s.sens_num}".format(s=self)
        res["id"] = self.sens_num
        res["meaning"] = self.meaning
        res["contexts"] = self.examples
        return res

    def empty(self):
        return len(self.meaning) == 0


class DanteParser(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self)

        self.in_sense = False
        self.in_meaning = False
        self.in_ex = False
        self.in_hwd = False

        self.curr_ex = ""
        self.curr_word = ""
        self.curr_id = 0
        self.in_mweblk = False
        self.curr_sense = DanteSense()

        self.senses = []

    def handle_starttag(self, tag, attrs):
        if tag == "p:mweblk":
            self.in_mweblk = True

        if self.in_mweblk:
            return

        if tag == "p:pos":
            self.curr_sense.pos = self._get_first_attr(attrs, "p:code")

        if tag == "p:hwd":
            self.in_hwd = True

        if tag == "p:meaning":
            self.in_meaning = True

        if tag == "p:ex":
            self.in_ex = True

    def handle_endtag(self, tag):
        if tag == "p:sensecont":
            if not self.curr_sense.empty():
                self.curr_id += 1
                self.curr_sense.sens_num = self.curr_id
                self.curr_sense.word = self.curr_word
                self.senses.append(self.curr_sense)
            self.curr_sense = DanteSense()

        if tag == "span":
            self.in_sense = False

        if tag == "p:hwd":
            self.in_hwd = False

        if tag == "p:meaning":
            self.in_meaning = False

        if tag == "p:mweblk":
            self.in_mweblk = False

        if tag == "p:ex":
            self.in_ex = False
            if self.curr_ex:
                self.curr_sense.examples.append(self.curr_ex.strip())
            self.curr_ex = ""

    def handle_data(self, data):
        data = data.decode("utf-8")

        if self.in_meaning:
            self.curr_sense.meaning += data

        if self.in_ex:
            self.curr_ex += data

        if self.in_hwd:
            self.curr_word += data

    def _get_first_attr(self, attrs, name):
        for attr, value in attrs:
            if attr == name:
                return value


def process_file(inp_path, out_path, filt_pos):
    with open(inp_path) as inp:
        parser = DanteParser()
        for line in inp:
            parser.feed(line)

    res = {}
    res["word"] = parser.curr_word.strip()
    res["meanings"] = []

    cur_id = 0
    for sense in parser.senses:
        if filt_pos and sense.pos.strip() != filt_pos:
            continue

        # id's should be continuous
        cur_id += 1
        sense.sens_num = cur_id
        res["meanings"].append(sense.to_json_d())

    with codecs.open(out_path, "w", "utf-8") as out:
        json.dump(
            res, out,
            sort_keys=True,
            ensure_ascii=False,
            indent=4,
        )


def process_dir(inp_dir, out_dir, filt_pos=None):
    if not os.path.isdir(out_dir):
        os.mkdir(out_dir)

    for fname in os.listdir(inp_dir):
        logging.info("processing " + fname)
        inp_path = os.path.join(inp_dir, fname)
        if not inp_path.endswith("html"):
            continue

        out_path = fname.split("_")[0] + ".json"
        out_path = os.path.join(out_dir, out_path)
        process_file(inp_path, out_path, filt_pos)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        #formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-i", "--inp",
        help="input dir with htmls",
    )
    parser.add_argument(
        "-f", "--filtpos",
        choices=("n"),
        help="filter part of speach",
    )
    parser.add_argument(
        "-o", "--out",
        help="output directory",
    )
    args = parser.parse_args()

    process_dir(args.inp, args.out, args.filtpos)


if __name__ == "__main__":
    # logging format description
    logging.basicConfig(
        level=logging.DEBUG,
        stream=sys.stderr,
        format=u'[%(asctime)s] %(levelname)-8s\t%(message)s',
    )
    logging.debug("Program starts")
    start_t = time.time()

    main()

    logging.debug(
        "Program ends. Elapsed time: {t}".format(
            t=datetime.timedelta(seconds=time.time() - start_t),
        )
    )

