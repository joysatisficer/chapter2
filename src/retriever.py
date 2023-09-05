from abc import abstractmethod, ABC
from typing import List

import faiss
import embedapi
import numpy as np
from thundersvm import SVC
from sklearn import svm
from asgiref.sync import sync_to_async

from chr_loader import load_chr


class AbstractIndex(ABC):
    def __int__(self):
        pass

    @abstractmethod
    async def add_data(self, data: list[str]):
        pass

    @abstractmethod
    async def query(self, query: str, k: int) -> list[str]:
        pass

    @staticmethod
    def process_string(string: str):
        return string


class SVMIndex(AbstractIndex):
    def __init__(self, transformer):
        self.transformer = transformer
        self.vectors = []
        self.strings = []

    def add_data(self, data: List[str]):
        processed = [self.process_string(s) for s in data]
        vectors = embedapi.encode_passages(self.transformer, processed)
        self.vectors.extend(vectors)
        self.strings.extend(data)

    def query(self, query: str, k: int) -> List[str]:
        vec_query = embedapi.encode_query(self.transformer, query)
        x = np.concatenate([vec_query[None, ...], self.vectors])
        y = np.zeros(len(self.vectors) + 1)
        y[0] = 1
        clf = SVC()
        clf.fit(x, y)
        similarities = [item[0] for item in clf.decision_function(x)]
        sorted_ix = np.argsort(-np.array(similarities))
        return [self.strings[index - 1] for index in sorted_ix[: k + 1] if index != 0]


class SciKitSVMIndex(SVMIndex):
    def query(self, query: str, k: int) -> List[str]:
        vec_query = embedapi.encode_query(self.transformer, query)
        x = np.concatenate([vec_query[None, ...], self.vectors])
        y = np.zeros(len(self.vectors) + 1)
        y[0] = 1
        clf = svm.LinearSVC(
            class_weight="balanced", verbose=False, max_iter=10000, tol=1e-6, C=0.1
        )
        clf.fit(x, y)
        similarities = [item for item in clf.decision_function(x)]
        sorted_ix = np.argsort(-np.array(similarities))
        return [self.strings[index - 1] for index in sorted_ix[: k + 1] if index != 0]


class KNNIndex(AbstractIndex):
    def __init__(self, transformer):
        self.transformer = transformer
        self.strings = []
        self.index = None

    # https://github.com/facebookresearch/faiss/wiki/Threads-and-asynchronous-calls
    # thread_sensitive=False is valid if locks are implemented properly
    @sync_to_async
    def add_data(self, data: List[str]):
        processed = [self.process_string(s) for s in data]
        self.strings.extend(data)
        embeddings = embedapi.encode_passages(self.transformer, processed)
        embeddings = np.copy(embeddings)
        faiss.normalize_L2(embeddings)
        if self.index is None:
            self.index = faiss.index_factory(
                embeddings.shape[1], "HNSW32", faiss.METRIC_INNER_PRODUCT
            )
        self.index.add(embeddings)

    @sync_to_async
    def query(self, query: str, k: int) -> List[str]:
        vec_query = np.array([embedapi.encode_query(self.transformer, query)])
        faiss.normalize_L2(vec_query)
        _, (embedding_ids,) = self.index.search(vec_query, k)
        documents = []
        seen_before = set()
        for embedding_id in embedding_ids:
            if embedding_id in seen_before:
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
