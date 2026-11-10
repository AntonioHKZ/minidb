"""B+ tree em memória usado como índice de chave primária.

Todas as chaves ficam nas folhas, que são encadeadas para permitir
varreduras por intervalo em O(log n + k). Os nós internos guardam apenas
chaves separadoras.

Convenções:
    * ``order`` é o número máximo de filhos de um nó interno.
    * Uma folha guarda no máximo ``order - 1`` chaves.
    * Para todo nó interno com chaves k0..kn-1, a subárvore ``children[i]``
      contém chaves ``k[i-1] <= x < k[i]``.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterator
from typing import Any


class DuplicateKeyError(KeyError):
    pass


class _Leaf:
    __slots__ = ("keys", "values", "next")

    def __init__(self) -> None:
        self.keys: list[Any] = []
        self.values: list[Any] = []
        self.next: _Leaf | None = None

    is_leaf = True


class _Internal:
    __slots__ = ("keys", "children")

    def __init__(self) -> None:
        self.keys: list[Any] = []
        self.children: list[_Leaf | _Internal] = []

    is_leaf = False


class BPlusTree:
    def __init__(self, order: int = 32) -> None:
        if order < 3:
            raise ValueError("order deve ser >= 3")
        self.order = order
        self._max_keys = order - 1
        self._min_leaf_keys = self._max_keys // 2
        self._min_children = (order + 1) // 2
        self._root: _Leaf | _Internal = _Leaf()
        self._size = 0

    # ------------------------------------------------------------------ busca

    def __len__(self) -> int:
        return self._size

    def __contains__(self, key: Any) -> bool:
        leaf = self._find_leaf(key)
        i = bisect_left(leaf.keys, key)
        return i < len(leaf.keys) and leaf.keys[i] == key

    def get(self, key: Any, default: Any = None) -> Any:
        leaf = self._find_leaf(key)
        i = bisect_left(leaf.keys, key)
        if i < len(leaf.keys) and leaf.keys[i] == key:
            return leaf.values[i]
        return default

    def _find_leaf(self, key: Any) -> _Leaf:
        node = self._root
        while not node.is_leaf:
            node = node.children[bisect_right(node.keys, key)]
        return node  # type: ignore[return-value]

    def _leftmost_leaf(self) -> _Leaf:
        node = self._root
        while not node.is_leaf:
            node = node.children[0]
        return node  # type: ignore[return-value]

    def items(self) -> Iterator[tuple[Any, Any]]:
        leaf: _Leaf | None = self._leftmost_leaf()
        while leaf is not None:
            yield from zip(leaf.keys, leaf.values)
            leaf = leaf.next

    def keys(self) -> Iterator[Any]:
        for k, _ in self.items():
            yield k

    def range(
        self,
        low: Any = None,
        high: Any = None,
        include_low: bool = True,
        include_high: bool = True,
    ) -> Iterator[tuple[Any, Any]]:
        """Itera em ordem sobre os pares com ``low <= key <= high``.

        ``None`` em ``low``/``high`` significa intervalo aberto naquele lado.
        """
        if low is None:
            leaf: _Leaf | None = self._leftmost_leaf()
            i = 0
        else:
            leaf = self._find_leaf(low)
            i = bisect_left(leaf.keys, low) if include_low else bisect_right(leaf.keys, low)

        while leaf is not None:
            while i < len(leaf.keys):
                k = leaf.keys[i]
                if high is not None and (k > high or (k == high and not include_high)):
                    return
                yield k, leaf.values[i]
                i += 1
            leaf = leaf.next
            i = 0

    # --------------------------------------------------------------- inserção

    def insert(self, key: Any, value: Any, replace: bool = False) -> None:
        split = self._insert(self._root, key, value, replace)
        if split is not None:
            sep, right = split
            new_root = _Internal()
            new_root.keys = [sep]
            new_root.children = [self._root, right]
            self._root = new_root

    def __setitem__(self, key: Any, value: Any) -> None:
        self.insert(key, value, replace=True)

    def _insert(self, node, key, value, replace):
        if node.is_leaf:
            i = bisect_left(node.keys, key)
            if i < len(node.keys) and node.keys[i] == key:
                if not replace:
                    raise DuplicateKeyError(key)
                node.values[i] = value
                return None
            node.keys.insert(i, key)
            node.values.insert(i, value)
            self._size += 1
            if len(node.keys) > self._max_keys:
                return self._split_leaf(node)
            return None

        i = bisect_right(node.keys, key)
        split = self._insert(node.children[i], key, value, replace)
        if split is None:
            return None
        sep, right = split
        node.keys.insert(i, sep)
        node.children.insert(i + 1, right)
        if len(node.children) > self.order:
            return self._split_internal(node)
        return None

    @staticmethod
    def _split_leaf(leaf: _Leaf):
        mid = len(leaf.keys) // 2
        right = _Leaf()
        right.keys = leaf.keys[mid:]
        right.values = leaf.values[mid:]
        leaf.keys = leaf.keys[:mid]
        leaf.values = leaf.values[:mid]
        right.next = leaf.next
        leaf.next = right
        return right.keys[0], right

    @staticmethod
    def _split_internal(node: _Internal):
        mid = len(node.keys) // 2
        sep = node.keys[mid]
        right = _Internal()
        right.keys = node.keys[mid + 1 :]
        right.children = node.children[mid + 1 :]
        node.keys = node.keys[:mid]
        node.children = node.children[: mid + 1]
        return sep, right

    # ---------------------------------------------------------------- remoção

    def delete(self, key: Any) -> Any:
        value = self._delete(self._root, key)
        root = self._root
        if not root.is_leaf and len(root.children) == 1:
            self._root = root.children[0]
        self._size -= 1
        return value

    def __delitem__(self, key: Any) -> None:
        self.delete(key)

    def _delete(self, node, key):
        if node.is_leaf:
            i = bisect_left(node.keys, key)
            if i >= len(node.keys) or node.keys[i] != key:
                raise KeyError(key)
            node.keys.pop(i)
            return node.values.pop(i)

        i = bisect_right(node.keys, key)
        child = node.children[i]
        value = self._delete(child, key)
        if self._underflow(child):
            self._rebalance(node, i)
        return value

    def _underflow(self, node) -> bool:
        if node.is_leaf:
            return len(node.keys) < self._min_leaf_keys
        return len(node.children) < self._min_children

    def _rebalance(self, parent: _Internal, i: int) -> None:
        child = parent.children[i]
        left = parent.children[i - 1] if i > 0 else None
        right = parent.children[i + 1] if i + 1 < len(parent.children) else None

        if child.is_leaf:
            if left is not None and len(left.keys) > self._min_leaf_keys:
                child.keys.insert(0, left.keys.pop())
                child.values.insert(0, left.values.pop())
                parent.keys[i - 1] = child.keys[0]
            elif right is not None and len(right.keys) > self._min_leaf_keys:
                child.keys.append(right.keys.pop(0))
                child.values.append(right.values.pop(0))
                parent.keys[i] = right.keys[0]
            elif left is not None:
                left.keys.extend(child.keys)
                left.values.extend(child.values)
                left.next = child.next
                del parent.keys[i - 1]
                del parent.children[i]
            else:
                assert right is not None
                child.keys.extend(right.keys)
                child.values.extend(right.values)
                child.next = right.next
                del parent.keys[i]
                del parent.children[i + 1]
            return

        if left is not None and len(left.children) > self._min_children:
            child.keys.insert(0, parent.keys[i - 1])
            child.children.insert(0, left.children.pop())
            parent.keys[i - 1] = left.keys.pop()
        elif right is not None and len(right.children) > self._min_children:
            child.keys.append(parent.keys[i])
            child.children.append(right.children.pop(0))
            parent.keys[i] = right.keys.pop(0)
        elif left is not None:
            left.keys.append(parent.keys[i - 1])
            left.keys.extend(child.keys)
            left.children.extend(child.children)
            del parent.keys[i - 1]
            del parent.children[i]
        else:
            assert right is not None
            child.keys.append(parent.keys[i])
            child.keys.extend(right.keys)
            child.children.extend(right.children)
            del parent.keys[i]
            del parent.children[i + 1]

    # ------------------------------------------------------------- depuração

    def height(self) -> int:
        h, node = 1, self._root
        while not node.is_leaf:
            node = node.children[0]
            h += 1
        return h

    def check_invariants(self) -> None:
        """Valida a estrutura da árvore. Usado nos testes."""
        leaf_depths: set[int] = set()

        def walk(node, lo, hi, depth, is_root):
            if lo is not None:
                assert all(k >= lo for k in node.keys), "chave abaixo do limite"
            if hi is not None:
                assert all(k < hi for k in node.keys), "chave acima do limite"
            assert node.keys == sorted(node.keys), "chaves fora de ordem"
            if node.is_leaf:
                assert len(node.keys) == len(node.values)
                assert len(node.keys) <= self._max_keys
                if not is_root:
                    assert len(node.keys) >= self._min_leaf_keys, "folha com underflow"
                leaf_depths.add(depth)
                return
            assert len(node.children) == len(node.keys) + 1
            assert len(node.children) <= self.order
            if not is_root:
                assert len(node.children) >= self._min_children, "nó interno com underflow"
            bounds = [lo, *node.keys, hi]
            for j, c in enumerate(node.children):
                walk(c, bounds[j], bounds[j + 1], depth + 1, False)

        walk(self._root, None, None, 0, True)
        assert len(leaf_depths) <= 1, "folhas em profundidades diferentes"

        seen = list(self.keys())
        assert seen == sorted(seen) and len(seen) == self._size
