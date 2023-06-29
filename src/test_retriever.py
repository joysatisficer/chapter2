from retriever import SVMIndex


class TestSVMIndex:
    def setup(self):
        self.index = SVMIndex()

    def test_retrieval(self):
        self.index.add_data(["hello", "world"])
        strings = self.index.query("hi", 1)
        assert strings[0] == "hello"
