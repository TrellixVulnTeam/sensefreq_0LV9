#!/usr/bin/env python
# -*- encoding: utf-8 -*-

from collections import defaultdict
from functools import partial
from operator import itemgetter

import numpy as np
import scipy.cluster.vq
import sklearn.cluster

from utils import unitvec, word_re, lemmatize_s, v_closeness, \
    context_vector as _context_vector
from active_dict.loader import get_ad_word
from supervised import load_weights
import kmeans


def context_vector(word, ctx, weights=None):
    return _context_vector([w for w in ctx if w != word], weights=weights)[0]


class Method(object):
    def __init__(self, m, n_senses):
        self.m = m
        self.n_senses = n_senses
        context_vectors = self.m['context_vectors']
        self.contexts = [ctx for ctx, __ in context_vectors]
        self.features = np.array([v for __, v in context_vectors])

    def cluster(self):
        raise NotImplementedError

    def predict(self, vectors):
        raise NotImplementedError

    def _build_clusters(self, assignment, distances):
        clusters = defaultdict(list)
        for c, ctx, dist in zip(assignment, self.contexts, distances):
            clusters[c].append((ctx, dist))
        return clusters

    def _predict_knn(self, vectors, nn=10):
        vectors = np.array(vectors)
        similarity_matrix = np.dot(vectors, np.transpose(self.features))
        predictions = []
        for v in similarity_matrix:
            av = zip(self.assignment, v)
            av.sort(key=itemgetter(1), reverse=True)
            weighted_sims = defaultdict(float)
            for c, s in av[:nn]:
                weighted_sims[c] += s
            predictions.append(
                max(weighted_sims.items(), key=itemgetter(1))[0])
        return np.array(predictions)


class SCKMeans(Method):
    ''' K-means from scipy.
    '''
    def cluster(self):
        # features = whiten(features)  # FIXME?
        self.centroids, distortion = scipy.cluster.vq.kmeans(
            self.features, self.n_senses)
        assignment, distances = scipy.cluster.vq.vq(
            self.features, self.centroids)
        return self._build_clusters(assignment, distances)

    def predict(self, vectors):
        features = np.array(vectors)
        assignment, __ = scipy.cluster.vq.vq(features, self.centroids)
        return assignment


class KMeans(Method):
    ''' K-means from scikit-learn.
    '''
    method = sklearn.cluster.KMeans

    def cluster(self):
        self._c = self.method(n_clusters=self.n_senses)
        transformed = self._c.fit_transform(self.features)
        assignment = transformed.argmin(axis=1)
        distances = transformed.min(axis=1)
        return self._build_clusters(assignment, distances)

    def predict(self, vectors):
        return self._c.predict(np.array(vectors))


class MBKMeans(KMeans):
    ''' Mini-batch K-means - good in practice.
    '''
    method = partial(sklearn.cluster.MiniBatchKMeans, batch_size=10)


class SKMeans(Method):
    ''' Spherical K-means.
    '''
    def cluster(self):
        self._c = kmeans.KMeans(self.features, k=self.n_senses,
            metric='cosine', verbose=0)
        return self._cluster()

    def _cluster(self):
        assignment = self._c.Xtocentre
        distances = self._c.distances
        return self._build_clusters(assignment, distances)

    def predict(self, vectors):
        return [np.argmax(np.dot(self._c.centres, v)) for v in vectors]


class SKMeansADInit(SKMeans):
    ''' Initialize clusters with Active Dictionary contexts.
    '''
    def cluster(self):
        word = self.m['word']
        ad_descr = get_ad_word(word)
        ad_centers = get_ad_centers(word, ad_descr)
        self.mapping = {
            i: int(meaning['id'])
            for i, meaning in enumerate(ad_descr['meanings'])}
        # note that the clusters can drift to quite different positions
        centers = np.array([ad_centers[m['id']] for m in ad_descr['meanings']])
        self._c = kmeans.KMeans(
            self.features, centres=centers, metric='cosine', verbose=0)
        return self._cluster()


def get_ad_centers(word, ad_descr):
    centers = {}
    weights = load_weights(word)
    for meaning in ad_descr['meanings']:
        center = None
        for ctx in meaning['contexts']:
            ctx = [w for w in lemmatize_s(ctx.lower()) if word_re.match(w)]
            vector = context_vector(word, ctx, weights=weights)
            if vector is not None:
                if center is None:
                    center = vector
                else:
                    center += vector
        if center is not None:
            centers[meaning['id']] = unitvec(center)
    return centers


class SKMeansADMapping(SKMeans):
    ''' Do cluster mapping using Active Dictionary contexts.
    '''
    def cluster(self):
        clusters = super(SKMeansADMapping, self).cluster()
        word = self.m['word']
        ad_descr = get_ad_word(word)
        ad_centers = get_ad_centers(word, ad_descr)
        self.mapping = {}
        for ci, center in enumerate(self._c.centres):
            self.mapping[ci] = max(
                ((int(mid), v_closeness(center, m_center))
                    for mid, m_center in ad_centers.iteritems()),
                key=itemgetter(1))[0]
        return clusters


# Methods below are slow, bad for this task, or both


class Agglomerative(Method):
    def cluster(self):
        self._c = sklearn.cluster.AgglomerativeClustering(
            n_clusters=self.n_senses,
            affinity='cosine',
            linkage='average')
        assignment = self._c.fit_predict(self.features)
        distances = [0.0] * len(assignment)  # FIXME
        return self._build_clusters(assignment, distances)

    def predict(self, vectors):
        # TODO - use kNN?
        pass


class MeanShift(Method):
    def cluster(self):
        self._c = sklearn.cluster.MeanShift()
        assignment = self._c.fit_predict(self.features)
        distances = [0.0] * len(assignment)  # FIXME
        return self._build_clusters(assignment, distances)

    def predict(self, vectors):
        return self._c.predict(np.array(vectors))


class Spectral(Method):
    def cluster(self):
        self._c = sklearn.cluster.SpectralClustering(
            n_clusters=self.n_senses,
            affinity='cosine')
        self.assignment = self._c.fit_predict(self.features)
        distances = [0.0] * len(self.assignment)  # FIXME
        return self._build_clusters(self.assignment, distances)

    def predict(self, vectors):
        return self._predict_knn(vectors, 10)


class DBSCAN(Method):
    def cluster(self):
        self._c = sklearn.cluster.DBSCAN(
            metric='cosine', algorithm='brute', eps=0.3)
        self.assignment = self._c.fit_predict(self.features)
        distances = [0.0] * len(self.assignment)  # FIXME
        return self._build_clusters(self.assignment, distances)

    def predict(self, vectors):
        return self._predict_knn(vectors, 5)
