import threading
from abc import abstractmethod, ABC
from typing import List

import embedapi
import numpy as np
from asgiref.sync import sync_to_async as asyncify

from chr_loader import load_chr

EMPTY = tuple()


# todo: replace with icontract


class AbstractIndex(ABC):
    def __init__(self):
        self.frozen = False
        self.index = EMPTY

    @abstractmethod
    async def add_data(self, data: list[str], keys):
        assert len(data) == len(keys)
        if len(data) == 0:
            return True  # signal that caller should return immediately
        elif self.frozen:
            raise ValueError("Frozen index cannot be modified")

    @classmethod
    def dec_add_data(cls, fn):
        async def add_data(*args, **kwargs):
            if await cls.add_data(*args, **kwargs):
                return
            else:
                return await fn(*args, **kwargs)

        return add_data

    @abstractmethod
    async def query(self, query: str, k: int) -> list[str]:
        if self.index == EMPTY:
            return []

    @classmethod
    def dec_query(cls, fn):
        async def query(*args, **kwargs):
            value = await cls.query(*args, **kwargs)
            if value is not None:
                return value
            else:
                return await fn(*args, **kwargs)

        return query

    @staticmethod
    def process_string(string: str):
        return string

    def freeze(self):
        self.frozen = True


class SVMIndex(AbstractIndex):
    def __init__(self, transformer):
        super().__init__()
        self.transformer = transformer
        self.vectors = []
        self.strings = []

    @AbstractIndex.dec_add_data
    @asyncify
    def add_data(self, indexates: list[str], keys: list):
        vectors = embedapi.encode_passages(self.transformer, indexates)
        self.vectors.extend(vectors)
        self.strings.extend(keys)

    @AbstractIndex.dec_query
    @asyncify
    def query(self, query: str, k: int) -> List[str]:
        from sklearn.svm import LinearSVC

        vec_query = embedapi.encode_query(self.transformer, query)
        x = np.concatenate([vec_query[None, ...], self.vectors])
        y = np.zeros(len(self.vectors) + 1)
        y[0] = 1
        clf = LinearSVC(
            class_weight="balanced", verbose=False, max_iter=10000, tol=1e-6, C=0.1
        )
        clf.fit(x, y)
        similarities = [item for item in clf.decision_function(x)]
        sorted_ix = np.argsort(-np.array(similarities))
        return [self.strings[index - 1] for index in sorted_ix[: k + 1] if index != 0]


class KNNIndex(AbstractIndex):
    def __init__(self, transformer):
        super().__init__()
        self.transformer = transformer
        self.strings = []
        self.index = None
        self.lock = threading.Lock()

    @AbstractIndex.dec_add_data
    @asyncify(thread_sensitive=False)
    def add_data(self, indexates: list[str], keys: list):
        with self.lock:
            import faiss

            self.strings.extend(keys)
            embeddings = embedapi.encode_passages(self.transformer, indexates)
            embeddings = np.copy(embeddings)
            faiss.normalize_L2(embeddings)
            if self.index is None:
                self.index = faiss.index_factory(
                    embeddings.shape[1], "HNSW32", faiss.METRIC_INNER_PRODUCT
                )
                self.shape = embeddings.shape[1]
            self.index.add(embeddings)

    # https://github.com/facebookresearch/faiss/wiki/Threads-and-asynchronous-calls
    # thread_sensitive=False is valid only for CPU indexes
    @AbstractIndex.dec_query
    @asyncify(thread_sensitive=False)
    def query(self, query: str, k: int) -> List[str]:
        import faiss

        if query == "":
            # todo: instead of the center of this embedding space, this should return
            #  the average of all the points indexed
            vec_query = np.array([np.zeros(self.shape)], dtype=np.float32)
        else:
            vec_query = np.array([embedapi.encode_query(self.transformer, query)])
        faiss.normalize_L2(vec_query)
        _, (embedding_ids,) = self.index.search(vec_query, k)
        documents = []
        seen_before = set()
        for embedding_id in embedding_ids:
            if embedding_id in seen_before or embedding_id < 0:
                continue
            documents.append(self.strings[embedding_id])
            seen_before.add(embedding_id)
        return documents


if __name__ == "__main__":
    import streamlit as st

    st.title("Retrieval tester")
    character = st.selectbox("Character", ("monika", "tetration", "january"))
    st.write("Presets")
    cols = st.columns(3)
    if cols[0].button("Chapter 1 (default)"):
        st.session_state.representation_model = (
            "sentence-transformers/all-mpnet-base-v2"
        )
        st.session_state.algorithm = "KNN"
    if cols[1].button("Chapter 1 (SVM)"):
        st.session_state.representation_model = (
            "sentence-transformers/all-mpnet-base-v2"
        )
        st.session_state.algorithm = "SciKitSVM"
    if cols[2].button("Chapter 2 (SVM)"):
        st.session_state.representation_model = "intfloat/e5-large-v2"
        st.session_state.algorithm = "ThunderSVM"
    transformer = st.selectbox(
        "Representation model",
        (
            "intfloat/e5-large-v2",
            "intfloat/e5-large-v2:symmetric",
            "sentence-transformers/all-mpnet-base-v2",
            "text-embedding-ada-002",
        ),
        key="representation_model",
    )
    algorithm = st.selectbox(
        "Algorithm", ("ThunderSVM", "SciKitSVM", "KNN"), key="algorithm"
    )
    if algorithm == "ThunderSVM":
        index = SVMIndex(transformer)
    elif algorithm == "SciKitSVM":
        index = SciKitSVMIndex(transformer)
    else:
        index = KNNIndex(transformer)
    index.add_data(
        [
            s
            for s in load_chr(f"people/{character}/{character}.ego")
            if s != "" and not s.isspace() and AbstractIndex.process_string(s) != ""
        ]
    )
    default_query = "<Monika> I love helping people grow into stronger, better people!"
    st.table(
        [
            s.replace("\n", "¶")
            for s in index.query(
                AbstractIndex.process_string(st.text_input("Query", default_query)), 10
            )
        ]
    )
