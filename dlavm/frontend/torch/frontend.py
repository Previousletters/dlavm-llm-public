from typing import Any, List, Optional, Tuple, Dict
import operator
import os
import ctypes
import platform

import torch
import torch._dynamo as dynamo
from torch._functorch.aot_autograd import aot_module_simplified
import torch.utils._pytree as pytree
from .graph import Graph, TensorDType, TensorMeta
from .graph.operation import *
from .graph.transform import maxpool2d_simplify
from .graph.type import *

class DynamoCompiler:
    """
    Dynamo Compiler is one of the frontends of Buddy Compiler.
    Dynamo Compiler acts as a custom compiler for the TorchDynamo framework,
    which converts an FX Graph into an equivalent Buddy Graph and MLIR module.

    Attributes:
        imported_graphs: The imported graphs.
        imported_params: The imported parameters from the model.
    """

    def __init__(
        self,
        func_name: str = "forward",
        primary_registry: Optional[dict] = None,
        aot_autograd_decomposition: Optional[dict] = None,
        verbose=False,
    ) -> None:
        """
        Initializes the Dynamo Compiler.

        Args:
            func_name: The function name to be used.
            primary_registry (dict, optional): The primary operations registry.
            aot_autograd_decomposition (Optional[dict], optional):
            The ahead-of-time autograd decomposition dictionary.
            verbose (bool): Controls whether to print additional information for
                debugging purposes. The default value is False, indicating that
                no extra debug information will be printed.
        Attributes:
            _func_name: The function name to be used.
            _aot_autograd_decomposition (Optional[dict], optional):
            The ahead-of-time autograd decomposition dictionary.
            _verbose: The option for the verbosity option of output.
            _imported_graphs: The buddy graphs from dynamo importer.
            _ops_registry (dict, optional): The buddy operations' lower func
            registry.
            _imported_params: The model params extract from torch.
            _ops_map: The torch aten ops map with buddy ops.

        """
        # Make custom dynamo compiler take effect.
        dynamo.reset()
        # Initialize the attributes.
        if primary_registry is None:
            primary_registry = {}
        self._func_name = func_name
        self._aot_autograd_decomposition = aot_autograd_decomposition
        self._verbose = verbose
        self._imported_graphs = []
        self._ops_registry = {}
        self._imported_params = {}
        self._ops_map = {
            "output": OutputOp,
            "placeholder": PlaceholderOp,
            "arange.start": ArangeOp,
            "arange.default": ArangeOp,
            "unsqueeze.default": UnsqueezeOp,
            "view.default": ViewOp,
            "ones.default": OnesOp,
            "full.default": FullOp,
            "lt.Tensor": LessThanOp,
            "embedding.default": EmbeddingOp,
            "masked_fill.Scalar": MaskedFillOp,
            "slice.Tensor": SliceOp,
            "expand.default": ExpandOp,
            "_to_copy.default": ToCopyOp,
            "rsub.Scalar": RsubOp,
            "pow.Tensor_Scalar": PowOp,
            "mean.dim": MeanOp,
            "rsqrt.default": RsqrtOp,
            "mul.Tensor": MulOp,
            "mul.Scalar": MulOp,
            "t.default": TransposeOp,
            "mm.default": MatmulOp,
            "transpose.int": TransposeOp,
            "index.Tensor": IndexOp,
            "neg.default": NegOp,
            "cat.default": CatOp,
            "squeeze.dim": SqueezeOp,
            "bmm.default": BatchMatmulOp,
            "div.Tensor": DivOp,
            "_softmax.default": SoftmaxOp,
            "clone.default": CloneOp,
            "silu.default": SiluOp,
            "add.Tensor": AddOp,
            "addmm.default": AddMMOp,
            "permute.default": PermuteOp,
            "convert_element_type.default": ConvertElementTypeOp,
            "sum.dim_IntList": SumDimOp,
            "tanh.default": TanhOp,
            "sub.Tensor": SubOp,
            "var_mean.correction": VarMeanOp,
            "amax.default": AmaxOp,
            "select.int": SelectOp,
            "exp.default": ExpOp,
            "erf.default": ErfOp,
            "getitem": GetItemOp,
            "convolution.default": Conv2dOp,
            "max_pool2d_with_indices.default": MaxPool2dWithIndicesOp,
            "relu.default": ReluOp,
            "iota.default": IotaOp,
            "sigmoid.default": SigmoidOp,
            "scalar_tensor.default": ScalarTensorOp,
            "where.self": WhereOp,
            "sqrt.default": SqrtOp,
            "reciprocal.default": ReciprocalOp,
            "clamp_min.default": ClampMinOp,
            "clamp_max.default": ClampMaxOp,
            "randint.low": RandIntLowOp,
            "cos.default": CosOp,
            "sin.default": SinOp,
            "argmax.default": ArgMaxOp,
            "split.Tensor": SplitOp,
            "max.default": MaxOp,
            "gt.Scalar": GtOp,
            "_scaled_dot_product_flash_attention_for_cpu.default": ScaledDotProductFlashAttentionForCpuOp,
            "ge.Scalar": GeOp,
            "gt.Tensor": GreaterThanOp,
            "_unsafe_index.Tensor": UnsafeIndexOp,
            "_native_batch_norm_legit_functional.default": BatchNormOp,
            "_native_batch_norm_legit_no_training.default": BatchNormOp,
            "avg_pool2d.default": AvgPool2dOp,
            "arange.start_step": ArangeOp,
            "pow.Scalar": PowOp,
            "stack.default": StackOp,
            "split_with_sizes.default": SplitWithSizeOp,
            "_unsafe_view.default": ViewOp,
            "rotary_pos_emb.default": RotaryPosEmbOp,
            "native_layer_norm.default": LayerNormOp,
            "gelu.default": GeluOp,
            "_scaled_dot_product_efficient_attention.default":ScaledDotProductFlashAttentionForCpuOp,
        }

    @property
    def imported_graphs(self):
        """Returns the imported buddy graphs after compilation."""
        return self._imported_graphs

    @property
    def imported_params(self):
        """Returns the imported model params after compilation."""
        return self._imported_params

    def _torch_dtype_translate(self, dtype):
        if dtype == "torch.int64":
            return TensorDType.Int64
        if dtype == "torch.int32":
            return TensorDType.Int32
        if dtype == "torch.int8":
            return TensorDType.Int8
        if dtype == "torch.float16":
            return TensorDType.Float16
        if dtype == "torch.float32":
            return TensorDType.Float32
        if dtype == "torch.float64":
            return TensorDType.Float64
        if dtype == "torch.bool":
            return TensorDType.Bool
        if dtype == "torch.bfloat16":
            return TensorDType.BFloat16
        raise NotImplementedError(f"Unsupported dtype: {dtype}")

    def _create_node(
        self,
        gm_node_name: str,
        node_name: str,
        node_input: Tuple,
        node_users: List[str],
        node_output_shape: list = [],
        node_output_dtype: TensorDType = None,
        node_kwargs: Optional[Dict] = None,
    ):
        """
        Create buddy op node from torch aten op.

        Args:
            gm_node_name: The op node class map to buddy op by _ops_map.
            node_name: The op node name to be used.
            node_input: The args input to op node.
            node_output_shape: The list of the op node's output shape.
            node_output_dtype: The TensorDType enum type of the op node's output
            data type.
            node_kwargs: The restful attributes for op node.
        """
        op_class = self._ops_map.get(gm_node_name)
        if op_class is None:
            raise RuntimeError(f"{gm_node_name} not realized in _ops_map, please check!")
        buddy_node = op_class()
        buddy_node._name = node_name
        if gm_node_name == "output":
            for input_arg in node_input[0]:
                buddy_node.add_parent(str(input_arg))
                buddy_node.add_argument(str(input_arg))
            return buddy_node
        if len(node_input) and isinstance(node_input[0], (list, tuple)):
            if isinstance(node_input[0][0], torch.fx.Node):
                node_input = list(node_input[0]) + list(node_input[1:])
        for input_arg in node_input:
            if isinstance(input_arg, torch.fx.Node):
                buddy_node.add_argument(str(input_arg))
                buddy_node.add_parent(str(input_arg))
            elif isinstance(input_arg, torch.dtype):
                buddy_node.add_argument(
                    self._torch_dtype_translate(str(input_arg))
                )
            else:
                buddy_node.add_argument(input_arg)
        for user in node_users:
            buddy_node.add_children(user)
        if node_kwargs is None:
            node_kwargs = {}
        buddy_node._keyword_arguments.update(node_kwargs)
        if isinstance(node_output_shape, torch.Size):
            node_output_shape = list(node_output_shape)
        else:
            node_output_shape = [list(i) for i in node_output_shape]
        buddy_node._tensor_meta["shape"] = node_output_shape
        buddy_node._tensor_meta["dtype"] = node_output_dtype
        return buddy_node

    def _compile_fx(
        self, gm: torch.fx.GraphModule, inputs: List[torch.Tensor]
    ) -> Any:
        """
        Compiles the provided FX Graph to Buddy Graph.

        Args:
            gm (torch.fx.GraphModule): The GraphModule to be compiled.
            inputs (List[torch.Tensor]): The input tensors.

        Returns:
            dynamo_run: The function of the ahead-of-time compiled module,
            return for torchdynamo's call.
        """

        params = {
            **dict(gm.named_parameters(remove_duplicate=False)),
            **dict(gm.named_buffers(remove_duplicate=False)),
        }
        params_flat, _ = pytree.tree_flatten(params)

        inputs_pos = []
        # params_pos = []
        # buffers_pos = []
        for i, node in enumerate(gm.graph.nodes):
            if i >= len(inputs):
                break
            if not str(node).startswith("l_self"):
                inputs_pos.append(i)
            # elif "buffer" in str(node):
            #     buffers_pos.append(i)
            # else:
            #     params_pos.append(i)

        # params_flat = [inputs[i] for i in params_pos + buffers_pos]

        if self._verbose:
            print("Graph in tabular form:")
            gm.graph.print_tabular()

        def _compiler(_gm: torch.fx.GraphModule, _inputs: List[torch.Tensor]):
            """Compile a FX graph in Aten/Prims IR to MLIR."""
            nonlocal params_flat
            func_inputs = []
            for i in inputs_pos:
                # for inp in _inputs[len(params_flat) :]:
                inp = _inputs[len(params_flat)+i]
                inp_shape = inp.shape
                inp_dtype = self._torch_dtype_translate(str(inp.dtype))
                func_inputs.append(TensorMeta(inp_shape, inp_dtype))
            fake_params = []
            for param in params_flat:
                param_dtype = self._torch_dtype_translate(str(param.dtype))
                fake_params.append(TensorMeta(param.shape, param_dtype))
            graph = Graph(
                func_inputs,
                fake_params,
                self._ops_registry,
                self._func_name,
                DeviceType.CPU,
                self._verbose,
            )

            gm_nodes = []
            for i, node in enumerate(_gm.graph.nodes):
                gm_nodes.append(node)

            for gm_node in gm_nodes:
                node_users = []
                for user in gm_node.users.keys():
                    node_users.append(str(user))
                if gm_node.op == "placeholder":
                    node_dtype = self._torch_dtype_translate(
                        str(gm_node.meta["tensor_meta"].dtype)
                    )
                    buddy_node = self._create_node(
                        gm_node.op,
                        gm_node.name,
                        gm_node.args,
                        node_users,
                        gm_node.meta["tensor_meta"].shape,
                        node_dtype,
                    )

                elif gm_node.op == "output":
                    buddy_node = self._create_node(
                        gm_node.op, gm_node.name, gm_node.args, node_users
                    )

                elif gm_node.target is operator.getitem:
                    node_dtype = self._torch_dtype_translate(
                        str(gm_node.meta["tensor_meta"].dtype)
                    )
                    buddy_node = self._create_node(
                        str(gm_node.target.__name__),
                        gm_node.name,
                        gm_node.args,
                        node_users,
                        gm_node.meta["tensor_meta"].shape,
                        node_dtype,
                    )

                else:
                    tensor_meta = gm_node.meta.get("tensor_meta")
                    val = gm_node.meta.get("val")
                    # num_returns = len(gm_node.target._schema.returns)
                    num_returns = (
                        len(val)
                        if isinstance(val, list)
                        else len(gm_node.target._schema.returns)
                    )
                    if num_returns == 1:
                        node_dtype = self._torch_dtype_translate(
                            str(tensor_meta.dtype)
                        )
                        node_shape = tensor_meta.shape
                    elif num_returns > 1:
                        node_dtype = tuple(
                            [
                                self._torch_dtype_translate(str(val_item.dtype))
                                for val_item in val
                            ]
                        )
                        node_shape = tuple([val_item.shape for val_item in val])
                    else:
                        raise RuntimeError("Zero returns is not supported.")

                    buddy_node = self._create_node(
                        str(gm_node.target.__name__),
                        gm_node.name,
                        gm_node.args,
                        node_users,
                        node_shape,
                        node_dtype,
                        node_kwargs=gm_node.kwargs,
                    )

                graph.add_node(buddy_node)
            transform_list = [maxpool2d_simplify]
            graph.perform(transform_list)
            self._imported_graphs.append(graph)
            self._imported_params[graph] = params_flat
            return _gm.forward

        return aot_module_simplified(
            gm,
            inputs,
            fw_compiler=_compiler,
            decompositions=self._aot_autograd_decomposition,
        )


    def __call__(
        self, gm: torch.fx.GraphModule, inputs: List[torch.Tensor]
    ) -> Any:
        """
        A callable method that wraps around the `_compile_fx` method.

        Args:
            gm (torch.fx.GraphModule): The GraphModule to be compiled.
            inputs (List[torch.Tensor]): The input tensors.

        Returns:
            dynamo_run: The function of the ahead-of-time compiled module,
            return for torchdynamo's call.
        """
        return self._compile_fx(gm, inputs)

    def importer(self, model, *args, **kwargs) -> List:
        """
        Imports the provided model as MLIR module and flat parameters.

        Args:
            model: The model to be imported.
            args: Arguments for the model.
            kwargs: Keyword arguments for the model.

        Returns:
            imported_graphs: The imported buddy graphs.
        """
        model_opt = dynamo.optimize(self._compile_fx)(model)
        model_opt(*args, **kwargs)
        return self._imported_graphs
