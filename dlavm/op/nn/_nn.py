from ...adr.base import Op, Tensor, Tuple, DataEnum
from ... import ne

'''
@brief: MatMul op
  @args[0]: input feature data, [1, token, chin]
  @args[1]: weight data, [chout, chin]
  @Optional<args[2]>: batch norm weight, [chout*2]
  @Optional<args[3]>: res input feature data, [1, token, chout]
  @output: Tensor, [1, token, chout]
'''
def MVMRel(args, attrs):
    if len(args) not in [2, 3, 4]:
        return False, "too more arguments! support [2, 3, 4], found " + str(len(args))
    device = args[0].device
    dtype = args[0].dtype
    dshape, wshape = args[0].shape, args[1].shape
    if dshape[-1] != wshape[1]:
        return False, "weight shape should be [out_channels, in_channels]"
    oshape = [i for i in dshape]
    oshape[-1] = wshape[0]
    if len(args) > 2:
        if args[2].shape[-1] != wshape[0]*2 and args[2].shape[-1] != wshape[0]:
            return False, "bn weight shape should be [out_channels*2] or [out_channels]"
    if len(args) > 3:
        if args[3].shape != oshape:
            return False, "res shape should equal to out shape"
    if attrs.get("arg_max", 0):
        arg_max_tensor = Tensor([1, oshape[-2]], dtype, device)
        setattr(arg_max_tensor, "csb_read", 40)
        tensors = Tuple([Tensor(oshape, dtype, device), arg_max_tensor])
        return True, tensors
    return True, Tensor(oshape, dtype, device)

Op.Register("nn.mvm", MVMRel)
Op.Register("nn.mvm_f16xi4", MVMRel)


'''
@brief: MatMul F16xF16 op
  @args[0]: input 1 feature data, [head, token, chin]
  @args[1]: input 2 feature data, [head, chin, token]/[head, token, chin]
  @attrs: w_trp, if input 2 should transpose before mvm fp16xfp16
  @output: Tensor, [head, token, token]
'''
def MVMF16xF16Rel(args, attrs):
    if len(args) not in [2]:
        return False, "too more arguments! support [2], found " + str(len(args))
    device = args[0].device
    dtype, wtype = args[0].dtype, args[1].dtype
    dshape, wshape = args[0].shape, args[1].shape
    if len(dshape) < 3 or len(wshape) < 3:
        return False, "the shape of arguments should be [head, token, chin] or [head, 1, token, chin]"
    oshape = [i for i in dshape]
    if attrs.get("w_trp", 0):
        if dshape[-1] != wshape[-1]:
            return False, "the channel does not match, found: " + str(dshape[-1]) + " and " + str(wshape[-1])
        oshape[-1] = wshape[-2]
    else:
        if dshape[-1] != wshape[-2]:
            return False, "the channel does not match, found: " + str(dshape[-1]) + " and " + str(wshape[-2])
        oshape[-1] = wshape[-1]
    if dtype.dtype != DataEnum.fp16 or wtype.dtype != DataEnum.fp16:
        return False, "the dtype of arguments shoul be " + DataEnum.fp16
    return True, Tensor(oshape, dtype, device)

Op.Register("nn.mvm_f16xf16", MVMRel)


'''
@brief: Norm op
  @args[0]: input feature data, [1, token, chin]
  @args[1]: weight data, [chin]
  @args[2]: bias data, [chin]
  @output: Tensor, [1, token, chin]
'''
def NormRel(args, attrs):
    if len(args) not in [3]:
        return False, "error length arguments! support 3, found " + str(len(args))
    device = args[0].device
    dtype = args[0].dtype
    dshape, wshape, bshape = args[0].shape, args[1].shape, args[2].shape
    if dshape[-1] != wshape[-1] or len(wshape) > 1 or wshape[-1] != bshape[-1] or len(bshape):
        return False, "weight and bias shape should be [in_channels]"
    oshape = [i for i in dshape]
    return True, Tensor(oshape, dtype, device)

Op.Register("nn.norm", NormRel)


'''
@brief: Softmax op
  @args[0]: input feature data, [1, token, chin]
  @output: Tensor, [1, token, chin]
'''
def SoftmaxRel(args, attrs):
    if len(args) not in [1]:
        return False, "error length arguments! support 1, found " + str(len(args))
    device = args[0].device
    dtype = args[0].dtype
    dshape = args[0].shape
    oshape = [i for i in dshape]
    return True, Tensor(oshape, dtype, device)

Op.Register("nn.softmax", SoftmaxRel)


'''
@brief: Elementwise op
  @args[0]: input feature data 1, [x]
  @args[1]: input feature data 2, [x]
  @output: Tensor, [x]
'''
def ElementwiseRel(args, attrs):
    if len(args) not in [2]:
        return False, "error length arguments! support 2, found " + str(len(args))
    device = args[0].device
    dtype = args[0].dtype
    dshape, wshape = args[0].shape, args[1].shape
    if len(dshape) != len(wshape):
        return False, "arguments should have same shape"
    for i in range(len(dshape)):
        if isinstance(dshape[i], ne.Expr):
            # TODO: check dynamic shape
            continue
        elif dshape[i] != wshape[i]:
            return False, "arguments should have same shape"
    oshape = [i for i in dshape]
    return True, Tensor(oshape, dtype, device)

Op.Register("nn.elementwise", ElementwiseRel)


'''
@brief: Activate op
  @args[0]: input feature data, [head, win, channels]
  @args[1]: activate weight, [16x3]
  @output: Tensor, [head, win, channels]
'''
def ActivateRel(args, attrs):
    if len(args) not in [2]:
        return False, "error length arguments! support 2, found " + str(len(args))
    device = args[0].device
    dtype = args[0].dtype
    dshape, wshape = args[0].shape, args[1].shape
    oshape = [i for i in dshape]
    return True, Tensor(oshape, dtype, device)

Op.Register("nn.activate", ActivateRel)

