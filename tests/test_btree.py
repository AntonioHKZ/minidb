import random

import pytest

from minidb.storage.btree import BPlusTree, DuplicateKeyError


@pytest.mark.parametrize("order", [3, 4, 5, 8, 32])
def test_insert_sequential_and_search(order):
    tree = BPlusTree(order)
    for i in range(500):
        tree.insert(i, f"v{i}")
    tree.check_invariants()
    assert len(tree) == 500
    assert tree.get(123) == "v123"
    assert tree.get(9999) is None
    assert list(tree.keys()) == list(range(500))


def test_duplicate_key_raises_and_replace_works():
    tree = BPlusTree(4)
    tree.insert(1, "a")
    with pytest.raises(DuplicateKeyError):
        tree.insert(1, "b")
    tree[1] = "b"
    assert tree.get(1) == "b"
    assert len(tree) == 1


def test_delete_missing_key():
    tree = BPlusTree(4)
    tree.insert(1, "a")
    with pytest.raises(KeyError):
        tree.delete(2)


@pytest.mark.parametrize("order", [3, 4, 5, 7])
@pytest.mark.parametrize("seed", range(5))
def test_random_operations_against_dict(order, seed):
    """Compara a árvore com um dict comum após milhares de operações."""
    rng = random.Random(seed)
    tree = BPlusTree(order)
    reference: dict[int, int] = {}

    for step in range(3000):
        key = rng.randint(0, 400)
        if rng.random() < 0.55:
            tree[key] = step
            reference[key] = step
        elif key in reference:
            assert tree.delete(key) == reference.pop(key)
        if step % 250 == 0:
            tree.check_invariants()

    tree.check_invariants()
    assert list(tree.items()) == sorted(reference.items())

    for key in list(reference):
        tree.delete(key)
    tree.check_invariants()
    assert len(tree) == 0
    assert tree.height() == 1


def test_range_scan_bounds():
    tree = BPlusTree(4)
    for i in range(0, 100, 2):
        tree.insert(i, i)

    def keys(*args, **kwargs):
        return [k for k, _ in tree.range(*args, **kwargs)]

    assert keys(10, 20) == [10, 12, 14, 16, 18, 20]
    assert keys(10, 20, include_low=False, include_high=False) == [12, 14, 16, 18]
    assert keys(11, 15) == [12, 14]
    assert keys(None, 4) == [0, 2, 4]
    assert keys(94, None) == [94, 96, 98]
    assert keys(200, None) == []


def test_tree_height_is_logarithmic():
    tree = BPlusTree(4)
    for i in range(10_000):
        tree.insert(i, None)
    # com ordem 4 cada nível multiplica a capacidade por pelo menos 2
    assert tree.height() <= 14


def test_string_keys():
    tree = BPlusTree(3)
    words = ["pera", "uva", "maçã", "abacaxi", "banana", "kiwi", "limão"]
    for w in words:
        tree.insert(w, len(w))
    tree.check_invariants()
    assert list(tree.keys()) == sorted(words)
