#!/usr/bin/env python
import argparse
from collections import Counter
import os.path
import pickle
from typing import List, Iterator, Dict, Tuple

from keras.models import Model
from keras.layers import Dense, Dropout, Input, Embedding, LSTM, merge
import numpy as np


def corpus_reader(corpus: str) -> Iterator[str]:
    """ Iterate over words in corpus.
    """
    # assume lemmatized and tokenized corpus
    with open(corpus) as f:
        for line in f:
            for word in line.strip().split():
                yield word


def get_features(corpus: str, *, n_features: int) -> (int, List[str]):
    cached_filename = '{}.f{}.pkl'.format(corpus, n_features)
    if os.path.exists(cached_filename):
        with open(cached_filename, 'rb') as f:
            return pickle.load(f)
    print('Getting words...', end=' ', flush=True)
    counts = Counter(corpus_reader(corpus))
    words = [w for w, _ in counts.most_common(n_features)]
    n_tokens = sum(counts.values())
    result = n_tokens, words
    with open(cached_filename, 'wb') as f:
        pickle.dump(result, f)
    print('done')
    return result


def data_gen(corpus, *, words: [str], n_features: int, window: int,
             batch_size: int, random_masking: bool)\
        -> Iterator[Dict[str, np.ndarray]]:
    PAD = 0
    PAD_WORD = '<PAD>'
    UNK = 1
    words = words[:n_features - 2]  # for UNK and PAD
    idx_to_word = {word: idx for idx, word in enumerate(words, 2)}
    idx_to_word[PAD_WORD] = PAD

    def to_arr(contexts: List[Tuple[List[str], List[str], str]], idx: int)\
            -> np.ndarray:
        return np.array(
            [[idx_to_word.get(w, UNK) for w in context[idx]]
             for context in contexts],
            dtype=np.int32)

    buffer_max_size = 10000
    while True:
        buffer = []
        batch = []
        for word in corpus_reader(corpus):
            buffer.append(word)
            # TODO - some shuffling?
            if len(buffer) > 2 * window:
                left = buffer[-2 * window - 1 : -window - 1]
                output = buffer[-window - 1 : -window]
                right = buffer[-window:]
                if random_masking:
                    left, right = random_mask(left, right, PAD_WORD)
                batch.append((left, right, output))
            if len(batch) == batch_size:
                left, right = to_arr(batch, 0), to_arr(batch, 0)
                output = to_arr(batch, 2)[:,0]
                batch[:] = []
                yield [left, right], output
            if len(buffer) > buffer_max_size:
                buffer[: -2 * window] = []


def random_mask(left: List[str], right: List[str], pad: str)\
        -> (np.ndarray, np.ndarray):
    n_left = n_right = 0
    w = len(left)
    assert len(right) == w
    while not (n_left or n_right):
        n_left, n_right = [np.random.randint(w + 1) for _ in range(2)]
    left[: w - n_left] = [pad] * (w - n_left)
    right[n_right:] = [pad] * (w - n_right)
    assert len(left) == len(right) == w
    return left, right


def build_model(*, n_features: int, embedding_size: int, hidden_size: int,
                window: int, dropout: bool) -> Model:
    print('Building model...', end=' ', flush=True)
    left = Input(name='left', shape=(window,), dtype='int32')
    right = Input(name='right', shape=(window,), dtype='int32')
    embedding = Embedding(
        n_features, embedding_size, input_length=window, mask_zero=True)
    forward = LSTM(hidden_size)(embedding(left))
    backward = LSTM(hidden_size, go_backwards=True)(embedding(right))
    hidden_out = merge([forward, backward], mode='concat', concat_axis=-1)
    if dropout:
        hidden_out = Dropout(0.5)(hidden_out)
    output = Dense(n_features, activation='softmax')(hidden_out)
    model = Model(input=[left, right], output=output)
    model.compile(loss='sparse_categorical_crossentropy', optimizer='rmsprop')
    print('done')
    return model


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('corpus')
    parser.add_argument('--n-features', type=int, default=50000)
    parser.add_argument('--embedding-size', type=int, default=128)
    parser.add_argument('--hidden-size', type=int, default=64)
    parser.add_argument('--window', type=int, default=10)
    parser.add_argument('--batch-size', type=int, default=16)
    parser.add_argument('--n-epochs', type=int, default=1)
    parser.add_argument('--random-masking', action='store_true')
    parser.add_argument('--dropout', action='store_true')
    parser.add_argument('--save')
    args = parser.parse_args()
    print(vars(args))

    model = build_model(
        n_features=args.n_features,
        embedding_size=args.embedding_size,
        hidden_size=args.hidden_size,
        window=args.window,
        dropout=args.dropout,
    )

    n_tokens, words = get_features(args.corpus, n_features=args.n_features)
    model.fit_generator(
        generator=data_gen(
            args.corpus,
            words=words,
            window=args.window,
            n_features=args.n_features,
            batch_size=args.batch_size,
            random_masking=args.random_masking
        ),
        samples_per_epoch=n_tokens,
        nb_epoch=args.n_epochs)

    if args.save:
        model.save_weights(args.save, overwrite=True)


if __name__ == '__main__':
    main()
