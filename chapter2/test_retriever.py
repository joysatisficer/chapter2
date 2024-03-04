from retriever import SVMIndex


class TestSVMIndex:
    def setup_method(self):
        self.index = SVMIndex("intfloat/e5-large-v2")

    async def test_retrieval(self):
        await self.index.add_data(["hello", "world"], ["hellokey", ["worldkey"]])
        strings = await self.index.query("hi", 1)
        assert strings[0] == "hellokey"
