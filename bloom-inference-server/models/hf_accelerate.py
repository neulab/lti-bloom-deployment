from argparse import Namespace

import torch

from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer
from utils import print_rank_n

from .model import Model, get_downloaded_model_path


class HFAccelerateModel(Model):
    def __init__(self, args: Namespace) -> None:
        print_rank_n("Loading model...")

        downloaded_model_path = get_downloaded_model_path(args.model_name)

        self.tokenizer = AutoTokenizer.from_pretrained(downloaded_model_path)
        self.pad = self.tokenizer.pad_token_id

        kwargs = {
            "pretrained_model_name_or_path": downloaded_model_path,
            "device_map": "auto",
            "max_memory": get_max_memory_per_gpu_dict(args.dtype, args.model_name),
        }
        if args.dtype == torch.int8:
            # using LLM.int8()
            kwargs["load_in_8bit"] = True
        else:
            kwargs["torch_dtype"] = args.dtype

        # this is the CUDA device for the current process. This will be used
        # later to identify the GPU on which to transfer tensors
        self.model = AutoModelForCausalLM.from_pretrained(**kwargs)

        self.model.requires_grad_(False)
        self.model.eval()
        self.input_device = "cuda:0"

        print_rank_n("Model loaded")


def get_max_memory_per_gpu_dict(dtype, model_name):
    """try to generate the memory map based on what we know about the model and the available hardware"""

    # figure out the memory map - the minimum per gpu required to load the model
    n_gpus = torch.cuda.device_count()

    if (
        model_name == "bigscience/bloom"
        and n_gpus == 8
        and torch.cuda.get_device_properties(0).total_memory > 79 * 2**30
    ):
        # hand crafted optimized memory map for 8x80 setup over BLOOM
        # this works with bs=40
        if dtype in [torch.bfloat16, torch.float16]:
            max_memory_per_gpu = {
                0: "0GIB",
                1: "51GIB",
                2: "51GIB",
                3: "51GIB",
                4: "51GIB",
                5: "51GIB",
                6: "51GIB",
                7: "51GIB",
            }
        elif dtype == torch.int8:
            max_memory_per_gpu = {
                0: "0GIB",
                1: "26GIB",
                2: "26GIB",
                3: "26GIB",
                4: "26GIB",
                5: "26GIB",
                6: "26GIB",
                7: "26GIB",
            }
        print_rank_n("Max memory per gpu:", max_memory_per_gpu)
        return max_memory_per_gpu
    try:
        # model_params calculation, as we don't have a model yet to do:
        # model_params = sum(dict((p.data_ptr(), p.numel()) for p in model.parameters()).values())

        config = AutoConfig.from_pretrained(model_name)
        h = config.hidden_size
        l = config.n_layer
        v = config.vocab_size
        # from https://github.com/bigscience-workshop/bigscience/tree/6917a3b5fefcf439d3485ca184b4d9f6ab605150/math#model-sizing
        model_params = l * (12 * h**2 + 13 * h) + v * h + 4 * h
    except Exception:
        print_rank_n(f"The model {model_name} has a broken config file. Please notify the owner")
        raise

    if dtype == torch.int8:
        bytes = 1
    else:
        bytes = torch.finfo(dtype).bits / 8
    param_memory_total_in_bytes = model_params * bytes
    # add 5% since weight sizes aren't the same and some GPU may need more memory
    param_memory_per_gpu_in_bytes = int(param_memory_total_in_bytes / n_gpus * 1.10)
    print_rank_n(f"Estimating {param_memory_per_gpu_in_bytes/2**30:0.2f}GB per gpu for weights")

    # check the real available memory
    # load cuda kernels first and only measure the real free memory after loading (shorter by ~2GB)
    torch.ones(1).cuda()
    max_memory_per_gpu_in_bytes = torch.cuda.mem_get_info(0)[0]
    if max_memory_per_gpu_in_bytes < param_memory_per_gpu_in_bytes:
        raise ValueError(
            f"Unable to generate the memory map automatically as the needed estimated memory per gpu ({param_memory_per_gpu_in_bytes/2**30:0.2f}GB) is bigger than the available per gpu memory ({max_memory_per_gpu_in_bytes/2**30:0.2f}GB)"
        )

    max_memory_per_gpu = {i: param_memory_per_gpu_in_bytes for i in range(torch.cuda.device_count())}
    print("Max memory per gpu:", max_memory_per_gpu)
    return max_memory_per_gpu
