#!/usr/bin/env python
# -*- encoding: utf-8 -*-

import sys

import msgpackrpc
from gensim.models import Word2Vec

from conf import WORD2VEC_PORT


class Word2VecServer(object):
    def __init__(self, filename):
        self.model = Word2Vec.load(filename)
        self._total_count = sum(x.count for x in self.model.vocab.itervalues())

    def vec(self, w):
        w = w.decode('utf-8')
        try:
            return map(float, self.model[w])
        except KeyError:
            return None

    def count(self, w):
        w = w.decode('utf-8')
        try:
            return self.model.vocab[w].count
        except KeyError:
            return None

    def vecs(self, w_list):
        return [self.vec(w) for w in w_list]

    def vecs_counts(self, w_list):
        return [(self.vec(w), self.count(w)) for w in w_list]

    def counts(self, w_list):
        return [self.count(w) for w in w_list]

    def total_count(self):
        return self._total_count


if __name__ == '__main__':
    server = msgpackrpc.Server(Word2VecServer(sys.argv[1]))
    server.listen(msgpackrpc.Address('localhost', WORD2VEC_PORT))
    print 'running...'
    server.start()
