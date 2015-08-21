#!/usr/bin/env python
# -*- encoding: utf-8 -*-

from __future__ import division

import sys
import os.path
import codecs
from collections import defaultdict
from operator import itemgetter

import numpy as np

from utils import w2v_vecs, unitvec, load, save, read_stopwords


def cluster(context_vectors_filename,
        n_senses=12,
        method='cluster_kmeans',
        rebuild=False,
        ):
    m = load(context_vectors_filename)
    print
    print m['word']
    clusters = m.get(method)
    if rebuild or clusters is None:
        clusters = globals()[method](m, n_senses)
        m[method] = clusters
        save(m, context_vectors_filename)
    stopwords = read_stopwords('stopwords.txt')
    n_contexts = len(m['context_vectors'])
    for c, elements in clusters.iteritems():
        elements.sort(key=itemgetter(1))
        print
        print '#%d: %.2f' % (c + 1, len(elements) / n_contexts)
        for w, count in best_words(elements, m['word'], stopwords)[:10]:
            print count, w
        for ctx, dist in elements[:7]:
            print u'%.2f: %s' % (dist, u' '.join(ctx))


def best_words(elements, word, stopwords):
    counts = defaultdict(int)
    for ctx, __ in elements:
        for w in ctx:
            if w not in stopwords and w != word:
                counts[w] += 1
    return sorted(counts.iteritems(), key=itemgetter(1), reverse=True)


def cluster_kmeans(m, n_senses):
    from scipy.cluster.vq import vq, kmeans #, whiten
    contexts = [ctx for ctx, __ in m['context_vectors']]
    features = np.array([v for __, v in m['context_vectors']],
                        dtype=np.float32)
    # features = whiten(features)  # FIXME?
    centroids, distortion = kmeans(features, n_senses)
    print 'distortion', distortion
    assignment, distances = vq(features, centroids)
    # TODO - find "best" contexts
    clusters = defaultdict(list)
    for c, ctx, dist in zip(assignment, contexts, distances):
        clusters[c].append((ctx, dist))
    return clusters


def build_context_vectors(contexts_filename, word, out_filename):
    if os.path.isdir(contexts_filename):
        assert os.path.isfile(word)
        assert os.path.isdir(out_filename)
        with codecs.open(word, 'rb', 'utf-8') as f:
            for w in f:
                w = w.strip()
                build_context_vectors(
                    os.path.join(contexts_filename, w + '.txt'),
                    w,
                    os.path.join(out_filename, w + '.pkl'))
    else:
        if not isinstance(word, unicode):
            word = word.decode('utf-8')
        vectors = []
        seen = set()
        print word
        for ctx in iter_contexts(contexts_filename):
            key = ' '.join(ctx)
            if key not in seen:
                seen.add(key)
                v = context_vector(word, ctx)
                vectors.append((ctx, v))
        print len(vectors), 'contexts'
        save({'word': word, 'context_vectors': vectors}, out_filename)


def context_vector(word, ctx):
    vector = None
    w_to_get = [w for w in ctx if w != word]
    for v in w2v_vecs(w_to_get):
        if v is not None:
            if vector is None:
                vector = np.array(v, dtype=np.float32)
            else:
                vector += v
    if vector is not None:
        return unitvec(vector)


def iter_contexts(contexts_filename):
    with open(contexts_filename, 'rb') as f:
        for line in f:
            yield line.decode('utf-8').split()


if __name__ == '__main__':
    args = sys.argv[1:]
    if len(args) == 3:
        build_context_vectors(*args)
    elif len(args) == 1:
        cluster(*args)
    else:
        print 'Usage:'
        print 'To build context vectors:'
        print '    ./cluster.py contexts_filename word context_vectors.pkl'
        print 'or  ./cluster.py contexts_folder word_list vectors_folder'
        print 'To cluster context vectors:'
        print '    ./cluster.py context_vectors.pkl'
        sys.exit(-1)
