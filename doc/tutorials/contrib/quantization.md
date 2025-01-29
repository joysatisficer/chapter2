# Quantizing a model
## Selecting a quantization level
`Q5_K_M` is suggested. See also:
- [llama.cpp GitHub discussion](https://github.com/ggerganov/llama.cpp/discussions/2094#discussioncomment-6351796)
- [(Reddit thread) GGUFs quants can punch above their weights now](https://www.reddit.com/r/LocalLLaMA/comments/1993iro/ggufs_quants_can_punch_above_their_weights_now/)
- Please add more links with different perspectives!
## Method 0: Download an existing quantization
The other methods in this tutorial is intended for helping a user quantize a
model with publicly available weights from scratch. Most models have readily
available quantization available. Custom quantization allows customizing the
weights considered important and given more computation by providing a
calibration file ([read more](#imatrix-calibration-file)) that will influence
what the model is best at producing. If you want to use an open model quickly
without quantizing it yourself, use this method.
### Step 1: Find a matching huggingface repo
Search huggingface, either using your general-purpose search engine such as
Google or huggingface's own search. The repo name will usually end in "gguf."
### Step 2: Download the relevant file
Click the "Files" tab. The file will end in ".gguf" and select the file that
matches your quantization level.
## Method 1: [gguf-my-repo](https://huggingface.co/spaces/ggml-org/gguf-my-repo)
As of January 2025, this method only works for smaller models. It will fail for
>=30B parameter models.
### Step 1: Make a huggingface account
### Step 2: Sign in with huggingface on [gguf-my-repo](https://huggingface.co/spaces/ggml-org/gguf-my-repo)
### Step 3: Paste the model ID into the "Hub Model ID" field
### Step 4: Check "Use Imatrix Quantization"
4### Step 5: Upload a [calibration file](#imatrix-calibration-file)
### Step 6: Press "submit"
## Method 2: llama.cpp
> **Note for Elysium users:** This has already been set up on Elysium.
> If you already have access to Elysium, please ask for access to infer@elysium
> if you prefer to avoid doing the setup again yourself.
### Prerequisites
The documentation for this method assumes the following prerequisites.
You may be able to use it with some of these prerequisites unmet, but some steps
may require modification.
- At least 2.75 times as much disk space as the size of the model

It is unknown if these prerequisites are necessary, but the author had them:
- NVIDIA GPU
- Enough CPU RAM to load the model in at the original size (usually 16-bit)
### Step 1: Set up llama.cpp
1. [Download and build llama.cpp](https://github.com/ggerganov/llama.cpp/blob/master/docs/build.md)
2. Install Python dependencies, run this inside the llama.cpp directory:
```
# cd llama.cpp
python3 -m venv venv
source venv/bin/activate
pip3 install .
```
### Step 2: Download the model
1. Install [Git LFS](https://docs.github.com/en/repositories/working-with-files/managing-large-files/installing-git-large-file-storage) and run `git lfs install` to set it up
2. Clone the huggingface repo and download the weights
```
git clone https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct
git lfs pull
```
### Step 3: Convert to GGUF
```
./llama.cpp/venv/bin/python3 ./llama.cpp/convert_hf_to_gguf.py models/meta-llama/Meta-Llama-3-8B-Instruct
```
### Step 4: Calibrate imatrix
[Read more](#imatrix-calibration-file). This example uses kaetemi's file.
```
wget https://huggingface.co/spaces/polyverse/README/blob/main/ridiculous_tokens_c3o_v4lf.txt
./llama.cpp/build/bin/llama-imatrix -m models/meta-llama/Meta-Llama-3-8B-Instruct -f ridiculous_tokens_c3o_v4lf.txt 
```
### Step 5: Quantize the model
Pass the location of the imatrix file (usually `imatrix.dat`) and the result of
the previous step (ending in gguf) to the llama-quantize command.
```
./llama.cpp/build/bin/llama-quantize --imatrix imatrix.dat models/meta-llama/Meta-Llama-3-8B-Instruct/Meta-Llama-3-8B-Instruct-F16.gguf Q5_K_M
```
## Imatrix calibration file
Choose a calibration file which is used to determine which weights are
"important." Since it's used to determine which logits
to avoid quantizing, it's important to select text that is both diverse and
matches what you want to use the quantized model for.


[This calibration file](https://huggingface.co/spaces/polyverse/README/blob/main/ridiculous_tokens_c3o_v4lf.txt)
was created by kaetemi as part of the
[Polyverse](https://huggingface.co/polyverse/) project, and contains nonsense
from Claude Opus, LaTeX, and code.

[This calibration file](https://gist.github.com/bartowski1182/eb213dccb3571f863da82e99418f81e8)
is used by [bartowski1182](https://huggingface.co/bartowski) for their
quantizations. It uses text provided by Dampf on top of kalomaze.

[Lewdiculous](https://huggingface.co/Lewdiculous/) generates text using the
original, unquantized model and uses that as the calibration text.

<!-- TODO: Calibration file based on samples of Act I outputs. -->
