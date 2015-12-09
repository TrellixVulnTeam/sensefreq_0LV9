#!/usr/bin/env python
# -*- encoding: utf-8 -*-

from __future__ import division

import json
import os.path
import argparse
from collections import Counter
from operator import itemgetter

import tornado.ioloop
from tornado.web import url, RequestHandler, Application

from utils import avg
from active_dict.loader import get_ad_word


class BaseHandler(RequestHandler):
    def load(self, name, *path):
        path = [self.ad_root] + list(path) + [name.encode('utf-8') + '.json']
        with open(os.path.join(*path), 'rb') as f:
            return json.load(f)

    @property
    def ad_root(self):
        return self.application.settings['ad_root']


class IndexHandler(BaseHandler):
    def get(self):
        root = self.ad_root
        context_paths = []
        for ctx_path in os.listdir(root):
            if os.path.isfile(os.path.join(root, ctx_path, 'summary.json')):
                context_paths.append(ctx_path)
        self.render(
            'templates/index.html',
            context_paths=context_paths,
            )


class WordsHandler(BaseHandler):
    def get(self, ctx_path):
        summary = self.load('summary', ctx_path)
        words_senses = []
        homonyms = self.get_argument('homonyms', None)
        for word, winfo in summary.iteritems():
            if (homonyms == 'yes' and not winfo['is_homonym']) or \
               (homonyms == 'no' and winfo['is_homonym']):
                continue
            words_senses.append(dict(
                winfo,
                word=word,
                senses=sorted_senses(winfo['senses']),
                ))
        words_senses.sort(key=itemgetter('word'))
        self.render(
            'templates/words.html',
            ctx_path=ctx_path,
            words_senses=words_senses,
            homonyms=homonyms,
            **statistics(words_senses)
            )

COLORS = ['red', 'orange', 'yellow', 'green', 'cyan', 'blue', 'violet']


def sorted_senses(senses):
    for id_, sense in senses.iteritems():
        sense['id'] = id_
        sense['color'] = COLORS[(int(id_) - 1) % len(COLORS)]
    return sorted(senses.itervalues(), key=lambda s: s['freq'], reverse=True)


def statistics(words_senses):
    sense_freq = lambda idx: avg([
        winfo['senses'][idx]['freq'] for winfo in words_senses
        if len(winfo['senses']) > idx])
    sense_avg = lambda min_freq: avg([
        float(sum(s['freq'] >= min_freq for s in winfo['senses']))
        for winfo in words_senses])
    min_sense_threshold = 0.15
    max_sense_threshold = 0.8
    dominant_ratio = sum(
        winfo['senses'][0]['freq'] >= max_sense_threshold
        for winfo in words_senses if winfo['senses']) / len(words_senses)
    return dict(
        n_words=len(words_senses),
        first_sense_freq=sense_freq(0),
        second_sense_freq=sense_freq(1),
        avg_senses_th=sense_avg(min_sense_threshold),
        avg_senses=sense_avg(0.0),
        min_sense_threshold=min_sense_threshold,
        max_sense_threshold=max_sense_threshold,
        dominant_ratio=dominant_ratio,
        )


class CompareHandler(BaseHandler):
    def get(self, ctx_path_1, ctx_path_2):
        summary1, summary2 = [
            self.load('summary', path) for path in [ctx_path_1, ctx_path_2]]
        self.render(
            'templates/compare.html',
            ctx_path_1=ctx_path_1,
            ctx_path_2=ctx_path_2,
            **compare_statistics(summary1, summary2)
            )


def compare_statistics(summary1, summary2):
    common_words = set(summary1).intersection(summary2)
    n_same = n_almost_same = 0
    differences = []
    almost_threshold = 0.15
    for w in common_words:
        senses1, senses2 = [sorted_senses(winfo['senses'])
                            for winfo in [summary1[w], summary2[w]]]
        if senses1 and senses2:
            id1, id2 = senses1[0]['id'], senses2[0]['id']
            if id1 == id2:
                n_same += 1
            else:
                get_f = lambda ss, id_: \
                    [s for s in ss if s['id'] == id_][0]['freq']
                try:
                    values = [get_f(ss, id_) for ss in [senses1, senses2]
                              for id_ in [id1, id2]]
                except IndexError:
                    values = None
                if values and max(values) - min(values) < almost_threshold:
                    n_almost_same += 1
                else:
                    differences.append((w, senses1, senses2))
    differences.sort(key=itemgetter(0))
    n = len(common_words)
    return dict(
        same_first_ratio=n_same / n,
        almost_same_first_ratio=(n_same + n_almost_same) / n,
        n_common_words=n,
        differences=differences,
        almost_threshold=almost_threshold,
        )


class WordHandler(BaseHandler):
    def get(self, ctx_path, word):
        ctx = self.load(word, ctx_path.encode('utf-8'))
        contexts = ctx['contexts']
        parsed = get_ad_word(word, self.ad_root)
        sense_by_id = {m['id']: m for m in parsed['meanings']}
        counts = Counter(ans for _, ans in contexts)
        self.render(
            'templates/word.html',
            word=parsed['word'],
            senses=sorted(
                (sid, sense_by_id[sid], count / len(contexts))
                for sid, count in counts.iteritems()),
            contexts=contexts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('ad_root')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--debug', action='store_true')
    args = parser.parse_args()
    application = Application([
        url(r'/', IndexHandler, name='index'),
        url(r'/c/([^/]+)/([^/]+)', CompareHandler, name='compare'),
        url(r'/w/([^/]+)', WordsHandler, name='words'),
        url(r'/w/([^/]+)/(.*)', WordHandler, name='word'),
        ],
        static_path='./active_dict/static',
        **vars(args)
    )
    application.settings['ad_root'] = args.ad_root.rstrip(os.sep)
    application.listen(args.port)
    tornado.ioloop.IOLoop.current().start()


if __name__ == '__main__':
    main()
