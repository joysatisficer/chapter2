from retriever import SVMIndex


class TestSVMIndex:
    def setup_method(self):
        self.index = SVMIndex("intfloat/e5-large-v2")

    def test_retrieval(self):
        self.index.add_data(["hello", "world"])
        strings = self.index.query("hi", 1)
        assert strings[0] == "hello"
