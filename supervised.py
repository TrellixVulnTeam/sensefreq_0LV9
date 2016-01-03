#!/usr/bin/env python
# -*- encoding: utf-8 -*-

from __future__ import division

import os
import sys
import random
import codecs
from collections import defaultdict, Counter
import itertools
import argparse
from operator import itemgetter

import numpy as np
from sklearn.mixture import GMM
from sklearn.manifold import TSNE
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

from utils import word_re, lemmatize_s, avg, avg_w_bounds, v_closeness, \
    context_vector, jensen_shannon_divergence, \
    bool_color, blue, magenta, bold_if
from semeval2007 import load_semeval2007


def get_ans_test_train(filename, n_train=None, test_ratio=None):
    assert n_train or test_ratio
    senses, w_d = get_labeled_ctx(filename)
    counts = defaultdict(int)
    for __, ans in w_d:
        counts[ans] += 1
    if n_train is None:
        n_test = int(len(w_d) * test_ratio)
    else:
        n_test = len(w_d) - n_train
    random.shuffle(w_d)
    return (
        {ans: (meaning, counts[ans]) for ans, meaning in senses.iteritems()},
        w_d[:n_test],
        w_d[n_test:])


def get_labeled_ctx(filename):
    ''' Read results from two annotators, return only contexts
    where both annotators agree on the meaning and it is defined.
    '''
    w_d = []
    with open(filename, 'rb') as f:
        senses = {}
        for i, line in enumerate(f, 1):
            row = filter(None, line.decode('utf-8').strip().split('\t'))
            try:
                if line.startswith('\t'):
                    if len(row) == 3:
                        meaning, ans, ans2 = row
                        assert ans == ans2
                    else:
                        meaning, ans = row
                    senses[ans] = meaning
                else:
                    other = str(len(senses) - 1)
                    if len(row) == 5:
                        before, word, after, ans1, ans2 = row
                        if ans1 == ans2:
                            ans = ans1
                        else:
                            continue
                    else:
                        before, word, after, ans = row
                    if ans != '0' and ans != other:
                        w_d.append(((before, word, after), ans))
            except ValueError:
                print 'error on line', i
                raise
    return senses, w_d


class SupervisedModel(object):
    def __init__(self, train_data,
            weights=None, excl_stopwords=True, verbose=False, window=None,
            w2v_weights=None):
        self.train_data = train_data
        self.examples = defaultdict(list)
        for x, ans in self.train_data:
            self.examples[ans].append(x)
        self.verbose = verbose
        self.window = window
        self.excl_stopwords = excl_stopwords
        self.weights = weights
        self.w2v_weights = w2v_weights
        self.sense_vectors = None
        self.context_vectors = {
            ans: np.array([cv for cv in map(self.cv, xs) if cv is not None])
            for ans, xs in self.examples.iteritems()}
        if self.examples:
            self.dominant_sense = max(
                ((ans, len(ex)) for ans, ex in self.examples.iteritems()),
                key=itemgetter(1))[0]

    def cv(self, (before, word, after)):
        word, = lemmatize_s(word)
        get_words = lambda s: [
            w for w in lemmatize_s(s) if word_re.match(w) and w != word]
        before, after = map(get_words, [before, after])
        if self.window:
            before, after = before[-self.window:], after[:self.window]
        words = before + after
        cv, w_vectors, w_weights = context_vector(
            words, excl_stopwords=self.excl_stopwords,
            weights=self.weights,
            weight_word=word if self.w2v_weights else None)
        if self.verbose and self.weights is not None and self.sense_vectors:
            print_verbose_repr(
                words, w_vectors, w_weights,
                sense_vectors=self.sense_vectors)
        return cv

    def get_train_accuracy(self, verbose=None):
        if not self.train_data:
            return 0
        if verbose is not None:
            is_verbose = self.verbose
            self.verbose = verbose
        n_correct = sum(ans == self(x) for x, ans in self.train_data)
        accuracy = n_correct / len(self.train_data)
        if verbose is not None:
            self.verbose = is_verbose
        return accuracy


class SphericalModel(SupervisedModel):
    def __init__(self, *args, **kwargs):
        super(SphericalModel, self).__init__(*args, **kwargs)
        self.sense_vectors = {ans: cvs.mean(axis=0)
            for ans, cvs in self.context_vectors.iteritems()
            if cvs.any()}

    def __call__(self, x, c_ans=None, with_confidence=False):
        v = self.cv(x)
        if v is None:
           #print >>sys.stderr, 'context vector is None:', ' '.join(x)
            m_ans = self.dominant_sense
            return (m_ans, 0.0) if with_confidence else m_ans
        ans_closeness = [
            (ans, v_closeness(v, sense_v))
            for ans, sense_v in self.sense_vectors.iteritems()]
        m_ans = max(ans_closeness, key=itemgetter(1))[0]
        if self.verbose:
            print ' '.join(x)
            print ' '.join(
                '%s: %s' % (ans, bold_if(ans == m_ans, '%.3f' % cl))
                for ans, cl in sorted(ans_closeness, key=sense_sort_key))
            if c_ans is not None:
                print 'correct: %s, model: %s, %s' % (
                        c_ans, m_ans, bool_color(c_ans == m_ans))
        if with_confidence:
            closeness = map(itemgetter(1), ans_closeness)
            closeness.sort(reverse=True)
            confidence = closeness[0] - closeness[1] if len(closeness) >= 2 \
                         else 1.0
        return (m_ans, confidence) if with_confidence else m_ans


class GMMModel(SupervisedModel):
    def __init__(self, *args, **kwargs):
        super(GMMModel, self).__init__(*args, **kwargs)
        self.senses = np.array(self.context_vectors.keys())
        self.classifier = GMM(
            n_components=len(self.context_vectors),
            covariance_type='full', init_params='wc')
        self.classifier.means_ = np.array([
            self.sense_vectors[ans] for ans in self.senses])
        x_train = np.array(
            list(itertools.chain(*self.context_vectors.values())))
        self.classifier.fit(x_train)

    def __call__(self, x):
        v = self.cv(x)
        return self.senses[self.classifier.predict(v)[0]]


class KNearestModel(SupervisedModel):
    def __init__(self, *args, **kwargs):
        self.k_nearest = kwargs.pop('k_nearest', 5)
        super(KNearestModel, self).__init__(*args, **kwargs)

    def __call__(self, x):
        v = self.cv(x)
        ans_closeness = sorted(
            ((ans, (v_closeness(v, _v)))
            for ans, context_vectors in self.context_vectors.iteritems()
            for _v in context_vectors),
            key=lambda (_, cl): cl, reverse=True)
        ans_counts = defaultdict(int)
        for ans, _ in ans_closeness[:self.k_nearest]:
            ans_counts[ans] += 1
        return max(ans_counts.iteritems(), key=lambda (_, count): count)[0]


class SupervisedWrapper(SupervisedModel):
    ''' Supervised wrapper around cluster.Method.
    '''
    def __init__(self, model, **kwargs):
        self.model = model
        super(SupervisedWrapper, self).__init__(train_data=[], **kwargs)

    def __call__(self, x, ans=None, with_confidence=False):
        v = self.cv(x)
        cluster = self.model.predict([v])[0]
        m_ans = str(self.model.mapping.get(cluster))
        return (m_ans, 0.0) if with_confidence else m_ans


def print_verbose_repr(words, w_vectors, w_weights, sense_vectors=None):
    w_vectors = dict(zip(words, w_vectors))
    if sense_vectors is not None:
        def sv(w):
            closeness = [
                v_closeness(w_vectors[w], sense_v)
                if w_vectors[w] is not None else None
                for _, sense_v in sorted_senses(sense_vectors)]
            defined_closeness = filter(None, closeness)
            if defined_closeness:
                max_closeness = max(defined_closeness)
                return ':(%s)' % ('|'.join(
                    bold_if(cl == max_closeness, magenta('%.2f' % cl))
                    for cl in closeness))
            else:
                return ':(-)'
    else:
        sv = lambda _: ''
    print
    print ' '.join(
        bold_if(weight > 1., '%s:%s%s' % (w, blue('%.2f' % weight), sv(w)))
        for w, weight in zip(words, w_weights))


def evaluate(model, test_data, train_data, perplexity=False):
    test_on = test_data if not perplexity else train_data
    answers = []
    confidences = []
    for x, ans in test_on:
        model_ans, confidence = model(x, ans, with_confidence=True)
        answers.append((x, ans, model_ans))
        confidences.append(confidence)
    n = len(answers)
    estimate = get_accuracy_estimate(confidences)
    n_correct = sum(ans == model_ans for _, ans, model_ans in answers)
    counts = Counter(ans for _, ans, _ in answers)
    model_counts = Counter(model_ans for _, _, model_ans in answers)
    max_count_error = max(abs(counts[s] - model_counts[s]) for s in counts)
    all_senses = sorted(set(model_counts) | set(counts))
    js_div = jensen_shannon_divergence(
        [counts[s] for s in all_senses], [model_counts[s] for s in all_senses])
    return (n_correct / n, max_count_error / n, js_div, estimate, answers)


def get_accuracy_estimate(confidences):
    return 1.0 - sum(c < 0.05 for c in confidences) / len(confidences)


def get_baseline(labeled_data):
    sense_freq = defaultdict(int)
    for __, ans in labeled_data:
        sense_freq[ans] += 1
    return float(max(sense_freq.values())) / len(labeled_data)


def write_errors(answers, i, filename, senses):
    errors = get_errors(answers)
    model_counts = Counter(model_ans for _, _, model_ans in answers)
    error_counts = Counter((ans, model_ans) for _, ans, model_ans in errors)
    err_filename = filename[:-4] + ('.errors%d.tsv' % (i + 1))
    with codecs.open(err_filename, 'wb', 'utf-8') as f:
        _w = lambda *x: f.write('\t'.join(map(unicode, x)) + '\n')
        _w('ans', 'count', 'model_count', 'meaning')
        for ans, (sense, count) in sorted_senses(senses)[1:-1]:
            _w(ans, count, model_counts[ans], sense)
        _w()
        _w('ans', 'model_ans', 'n_errors')
        for (ans, model_ans), n_errors in sorted(
                error_counts.iteritems(), key=itemgetter(1), reverse=True):
            _w(ans, model_ans, n_errors)
        _w()
        _w('ans', 'model_ans', 'before', 'word', 'after')
        for (before, w, after), ans, model_ans in \
                sorted(errors, key=lambda x: x[-2:]):
            _w(ans, model_ans, before, w, after)


def sorted_senses(senses):
    return sorted(senses.iteritems(), key=sense_sort_key)

sense_sort_key = lambda (ans, _): int(ans)


def get_errors(answers):
    return filter(lambda (_, ans, model_ans): ans != model_ans, answers)


def load_weights(word, root='.'):
    filename = os.path.join(root, 'cdict', word + '.txt')
    if os.path.exists(filename):
        with codecs.open(filename, 'rb', 'utf-8') as f:
            return {w: float(weight) for w, weight in (l.split() for l in f)}
    else:
        print >>sys.stderr, 'Weight file "%s" not found' % filename


def show_tsne(model, answers, senses, word):
    ts = TSNE(2, metric='cosine')
    vectors = [model.cv(x) for x, _, _ in answers]
    reduced_vecs = ts.fit_transform(vectors)
    colors = list('rgbcmyk') + ['orange', 'purple', 'gray']
    ans_colors = {ans: colors[int(ans) - 1] for ans in senses}
    seen_answers = set()
    plt.clf()
    plt.rc('legend', fontsize=9)
    font = {'family': 'Verdana', 'weight': 'normal'}
    plt.rc('font', **font)
    for (_, ans, model_ans), rv in zip(answers, reduced_vecs):
        color = ans_colors[ans]
        seen_answers.add(ans)
        marker = 'o' if ans == model_ans else 'x'
        plt.plot(rv[0], rv[1], marker=marker, color=color, markersize=8)
    plt.axes().get_xaxis().set_visible(False)
    plt.axes().get_yaxis().set_visible(False)
    legend = [mpatches.Patch(color=ans_colors[ans], label=label[:25])
        for ans, (label, _) in senses.iteritems() if ans in seen_answers]
    plt.legend(handles=legend)
    plt.title(word)
    filename = word + '.pdf'
    print 'saving tSNE clustering to', filename
    plt.savefig(filename)


def main():
    parser = argparse.ArgumentParser()
    arg = parser.add_argument
    arg('path')
    arg('--write-errors', action='store_true')
    arg('--n-train', type=int, default=50)
    arg('--perplexity', action='store_true', help='test on train data')
    arg('--verbose', action='store_true')
    arg('--n-runs', type=int, default=4)
    arg('--tsne', action='store_true')
    arg('--window', type=int, default=10)
    arg('--semeval2007', action='store_true')
    arg('--no-weights', action='store_true')
    arg('--w2v-weights', action='store_true')
    args = parser.parse_args()

    if args.semeval2007:
        semeval2007_data = load_semeval2007(args.path)
        filenames = list(semeval2007_data)
    else:
        if os.path.isdir(args.path):
            filenames = [os.path.join(args.path, f) for f in os.listdir(args.path)
                        if f.endswith('.txt')]
        else:
            filenames = [args.path]
        filenames.sort()

    baselines = []
    accuracies = []
    freq_errors = []
    model_class = SphericalModel
    wjust = 20
    print u'\t'.join([
        'word'.ljust(wjust), 'senses', 'b-line',
        'train', 'test', 'freq', 'estimate'])
    for filename in filenames:
        if args.semeval2007:
            word = filename
        else:
            word = filename.split('/')[-1].split('.')[0].decode('utf-8')
        weights = None if (args.no_weights or args.w2v_weights) else \
                  load_weights(word)
        test_accuracy, train_accuracy, estimates, word_freq_errors = [], [], [], []
        if args.semeval2007:
            senses, test_data, train_data = semeval2007_data[word]
           #print '%s: %d senses, %d test, %d train' % (
           #    word, len(senses), len(test_data), len(train_data))
            baseline = get_baseline(test_data + train_data)
        else:
            baseline = get_baseline(get_labeled_ctx(filename)[1])
        random.seed(1)
        for i in xrange(args.n_runs):
            if not args.semeval2007:
                senses, test_data, train_data = \
                    get_ans_test_train(filename, n_train=args.n_train)
           #if i == 0:
           #    print '%s: %d senses' % (word, len(senses) - 2)  # "n/a" and "other"
           #    print '%d test samples, %d train samples' % (
           #        len(test_data), len(train_data))
            model = model_class(
                train_data, weights=weights, verbose=args.verbose,
                window=args.window, w2v_weights=args.w2v_weights)
            accuracy, max_freq_error, js_div, estimate, answers = evaluate(
                model, test_data, train_data, perplexity=args.perplexity)
            if args.tsne:
                show_tsne(model, answers, senses, word)
            if args.write_errors:
                write_errors(answers, i, filename, senses)
            test_accuracy.append(accuracy)
            estimates.append(estimate)
            train_accuracy.append(model.get_train_accuracy(verbose=False))
            word_freq_errors.append(max_freq_error)
        accuracies.extend(test_accuracy)
        freq_errors.extend(word_freq_errors)
        baselines.append(baseline)
        avg_fmt = avg_w_bounds if args.n_runs > 1 else \
            (lambda x: '%.2f' % avg(x))
        print u'%s\t%d\t%.2f\t%s\t%s\t%s\t%s' % (
            word.ljust(wjust), len(senses), baseline,
            avg_fmt(train_accuracy),
            avg_fmt(test_accuracy),
            avg_fmt(word_freq_errors),
            avg_fmt(estimates))
    print
    print 'baseline: %.3f' % avg(baselines)
    print 'test acc: %.3f' % avg(accuracies)
    print 'freq err: %.3f' % avg(freq_errors)
    if len(filenames) == 1:
        print '\n'.join('%s: %s' % (ans, s)
                        for ans, (s, _) in sorted_senses(senses))


if __name__ == '__main__':
    main()
