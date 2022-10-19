# LTI's Large Language Model Deployment

**TODO**: Add a description of the project.

This repo is a fork of the [huggingface](https://huggingface.co/)'s [BLOOM inference demos](https://github.com/huggingface/transformers-bloom-inference).

## Installation

```bash
pip install -e .
```

## Example API Usage

```python
import lti_llm_client

client = lti_llm_client.Client()
client.prompt("CMU's PhD students are")
```
