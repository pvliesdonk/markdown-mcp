"""Numpy-backed vector index for semantic (cosine similarity) search."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from markdown_vault_mcp.providers import EmbeddingProvider

try:
    import numpy as np

    _NUMPY_AVAILABLE = True
except ImportError:
    _NUMPY_AVAILABLE = False

logger = logging.getLogger(__name__)


class VectorIndexCompatibilityError(RuntimeError):
    """Raised when a persisted vector index is incompatible with current provider."""


class VectorIndex:
    """Cosine-similarity vector index backed by numpy.

    Stores embedding vectors as a 2-D numpy array (shape ``[n, dim]``)
    with normalised rows so that similarity queries reduce to a dot-product.
    A parallel ``list[dict]`` holds the per-row metadata.

    The index is serialised as two sidecar files:

    - ``{path}.npy`` — the embedding matrix.
    - ``{path}.json`` — row metadata plus index metadata.

    Args:
        provider: Initialised :class:`~markdown_vault_mcp.providers.EmbeddingProvider`
            used to embed query strings at search time.

    Raises:
        ImportError: If ``numpy`` is not installed.
    """

    def __init__(self, provider: EmbeddingProvider) -> None:
        """Initialise an empty VectorIndex.

        Args:
            provider: Embedding provider used for query embedding.

        Raises:
            ImportError: If ``numpy`` is not installed.
        """
        if not _NUMPY_AVAILABLE:
            raise ImportError(
                "VectorIndex requires 'numpy'. "
                "Install it with: pip install 'markdown-vault-mcp[embeddings]'"
            )
        self._provider = provider
        # Shape: (0, dim) — will grow with each add() call.
        self._embeddings: np.ndarray = np.empty((0, 0), dtype=np.float32)
        self._metadata: list[dict] = []

    # ------------------------------------------------------------------
    # Class-method constructor
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path, provider: EmbeddingProvider) -> VectorIndex:
        """Load a VectorIndex from sidecar files.

        Args:
            path: Base path; files ``{path}.npy`` and ``{path}.json``
                must exist.
            provider: Embedding provider to attach to the loaded index.

        Returns:
            A :class:`VectorIndex` populated with the stored embeddings
            and metadata.

        Raises:
            ImportError: If ``numpy`` is not installed.
            FileNotFoundError: If either sidecar file is missing.
        """
        if not _NUMPY_AVAILABLE:
            raise ImportError(
                "VectorIndex requires 'numpy'. "
                "Install it with: pip install 'markdown-vault-mcp[embeddings]'"
            )

        npy_path = path.with_suffix(".npy")
        json_path = path.with_suffix(".json")

        embeddings: np.ndarray = np.load(str(npy_path))
        with json_path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)

        metadata: list[dict]
        expected_provider = provider.provider_name
        expected_model = provider.model_name
        if isinstance(payload, list):
            metadata = payload
            logger.warning(
                "VectorIndex.load: legacy metadata format at %s without provider/model identity",
                path,
            )
        else:
            metadata = payload.get("rows", [])
            index_meta = payload.get("index_metadata", {})
            persisted_provider = index_meta.get("provider")
            persisted_model = index_meta.get("model")
            if (
                persisted_provider != expected_provider
                or persisted_model != expected_model
            ):
                raise VectorIndexCompatibilityError(
                    "Embedding provider/model mismatch for persisted index at "
                    f"{path}: stored provider={persisted_provider!r}, "
                    f"stored model={persisted_model!r}, "
                    f"current provider={expected_provider!r}, "
                    f"current model={expected_model!r}."
                )

        index = cls(provider)
        index._embeddings = embeddings
        index._metadata = metadata

        logger.info(
            "VectorIndex.load: loaded %d vectors from %s",
            len(metadata),
            path,
        )
        return index

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def count(self) -> int:
        """Number of embedding rows currently stored.

        Returns:
            Integer row count.
        """
        return len(self._metadata)

    def add(self, texts: list[str], metadata: list[dict]) -> int:
        """Embed ``texts`` and append rows to the index.

        Vectors are L2-normalised before storage so that similarity
        queries can use a plain dot product.

        If the provider raises during embedding, no state is modified —
        the index remains exactly as it was before the call.

        Args:
            texts: Texts to embed.  Length must equal ``len(metadata)``.
            metadata: Per-row dicts (keys: ``path``, ``title``, ``folder``,
                ``heading``, ``content``).  Each dict is stored verbatim.

        Returns:
            Number of rows added.

        Raises:
            ValueError: If ``len(texts) != len(metadata)``, ``texts`` is
                empty, or the new vectors' dimension does not match the
                dimension of vectors already stored in the index.
            RuntimeError: Propagated from the embedding provider.
        """
        if len(texts) != len(metadata):
            raise ValueError(
                f"texts and metadata must have the same length "
                f"(got {len(texts)} vs {len(metadata)})"
            )
        if not texts:
            return 0

        # Embed first — do NOT mutate state until this succeeds.
        raw: list[list[float]] = self._provider.embed(texts)
        vectors = np.array(raw, dtype=np.float32)

        # L2-normalise each row.
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        # Avoid division by zero for zero-magnitude vectors.
        norms = np.where(norms == 0, 1.0, norms)
        vectors = vectors / norms

        if self._embeddings.size == 0:
            self._embeddings = vectors
        else:
            existing_dim = self._embeddings.shape[1]
            new_dim = vectors.shape[1]
            if new_dim != existing_dim:
                raise ValueError(
                    f"Embedding dimension mismatch: existing index has dim={existing_dim}, "
                    f"but new vectors have dim={new_dim}. "
                    "All embeddings must use the same model and dimension."
                )
            self._embeddings = np.vstack([self._embeddings, vectors])

        self._metadata.extend(metadata)

        logger.debug(
            "VectorIndex.add: added %d rows (total=%d)", len(texts), self.count
        )
        return len(texts)

    def search(self, query: str, *, limit: int = 10) -> list[dict]:
        """Return the top-k most similar chunks for ``query``.

        Args:
            query: Natural-language search string.
            limit: Maximum number of results to return.

        Returns:
            List of metadata dicts ordered by descending cosine similarity.
            Each dict contains all stored metadata fields plus a ``score``
            key (float in ``[-1, 1]``).

        Raises:
            RuntimeError: Propagated from the embedding provider.
        """
        if self.count == 0:
            return []

        raw = self._provider.embed([query])
        q_vec = np.array(raw[0], dtype=np.float32)

        norm = np.linalg.norm(q_vec)
        if norm > 0:
            q_vec = q_vec / norm

        # Dot product against normalised rows = cosine similarity.
        scores: np.ndarray = self._embeddings @ q_vec

        k = min(limit, self.count)
        # argsort descending, then take the top-k indices.
        top_indices = np.argsort(scores)[::-1][:k]

        results: list[dict] = []
        for idx in top_indices:
            entry = dict(self._metadata[int(idx)])
            entry["score"] = float(scores[int(idx)])
            results.append(entry)

        return results

    def delete_by_path(self, path: str) -> int:
        """Remove all rows for a given document path.

        Args:
            path: Relative document path (e.g. ``"Journal/note.md"``).

        Returns:
            Number of rows removed.
        """
        if self.count == 0:
            return 0

        keep_mask = np.array(
            [m.get("path") != path for m in self._metadata], dtype=bool
        )
        removed = int(np.sum(~keep_mask))

        if removed == 0:
            return 0

        if np.all(~keep_mask):
            # All rows belong to this path — reset to empty.
            self._embeddings = np.empty((0, 0), dtype=np.float32)
            self._metadata = []
        else:
            self._embeddings = self._embeddings[keep_mask]
            self._metadata = [
                m for m, keep in zip(self._metadata, keep_mask, strict=True) if keep
            ]

        logger.debug(
            "VectorIndex.delete_by_path: removed %d rows for %s (remaining=%d)",
            removed,
            path,
            self.count,
        )
        return removed

    def save(self, path: Path) -> None:
        """Persist the index to sidecar files.

        Writes ``{path}.npy`` (the embedding matrix) and ``{path}.json``
        (the metadata list).  An empty index is saved as a zero-row array.

        Args:
            path: Base path for the sidecar files.  Parent directory must
                exist.
        """
        npy_path = path.with_suffix(".npy")
        json_path = path.with_suffix(".json")

        if self._embeddings.size == 0:
            # Save a zero-shape array so load() can always read it back.
            empty = np.empty((0, 0), dtype=np.float32)
            np.save(str(npy_path), empty)
        else:
            np.save(str(npy_path), self._embeddings)

        payload = {
            "rows": self._metadata,
            "index_metadata": {
                "provider": self._provider.provider_name,
                "model": self._provider.model_name,
                "dimension": (
                    int(self._embeddings.shape[1]) if self._embeddings.ndim == 2 else 0
                ),
            },
        }

        with json_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)

        logger.info(
            "VectorIndex.save: saved %d vectors to %s",
            self.count,
            path,
        )
