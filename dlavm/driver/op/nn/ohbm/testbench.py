from dlavm import ne
from dlavm.adr import Op, Attrs
from dlavm.device import ohbm_accel
from .... import ir
from ....ir import CSB_Write, CSB_Read, While
from ....basic import TestbenchSIM, Tasks

def get_vars(targets):
    vars = []
    func = lambda n : [i for i in n if i not in vars]
    for n in targets:
        if isinstance(n, list):
            vars += func(get_vars(n))
        elif isinstance(n, Attrs):
            vars += func(get_vars(n.values()))
        elif isinstance(n, ne.Expr):
            vars += func(n.get_vars(True))
    return vars

#####################################################################
@Tasks.Register("tb.nn.mvm_f16xi4.ohbm", ohbm_accel.OHBM)
def MVMF16xI4(args, output, attrs):
    if len(args) == 2:
        dtensor, wtensor = args[0], args[1]
        dshape, wshape = dtensor.shape, wtensor.shape
        daddr = dtensor.static_address
        waddr = wtensor.static_address
        oaddr = output[0].static_address
        macro_define = {
            "last_token" : attrs.get("last_token", 0),
            "Token" : dshape[-2],
            "RELU_EN" : 1 if attrs.get("relu") else 0,
            "Width_in" : dshape[-1],
            "Width_out" : wshape[0],
            # "DAT_IN_BASE_ADDR" : daddr,
            # "HBM_WT_BASE_ADDR" : waddr,
            # "DAT_OUT_BASE_ADDR" : oaddr,
        }
        return TestbenchSIM("testbench_HBM_MVM", macro_define)
    elif len(args) == 3:
        dtensor, wtensor = args[0], args[1]
        dshape, wshape = dtensor.shape, wtensor.shape
        daddr = dtensor.static_address
        waddr = wtensor.static_address
        oaddr = output[0].static_address
        macro_define = {
            "last_token" : attrs.get("last_token", 0),
            "Token" : dshape[-2],
            "RELU_EN" : 1 if attrs.get("relu") else 0,
            "Width_in" : dshape[-1],
            "Width_out" : wshape[0],
            # "DAT_IN_BASE_ADDR" : daddr,
            # "HBM_WT_BASE_ADDR" : waddr,
            # "DAT_OUT_BASE_ADDR" : oaddr,
        }
        return TestbenchSIM("testbench_HBM_MVM_BN", macro_define)
    else:
        raise RuntimeError("not support mvm with bn or res in tb")

@Op.RegisterAttrs("nn.mvm_f16xi4", "testbench", ohbm_accel.OHBM)
def tb_nn_mvm_f16xi4(args, output, attrs):
    if len(get_vars([args[0].shape, attrs])):
        raise RuntimeError("Unsupport dynamic symbol control in testbench simulation")
    device = args[0].device
    with ir.Function([]) as func:
        csbs = Tasks.Get("tb.nn.mvm_f16xi4.ohbm", device)(args, output, attrs)
        for csb in csbs:
            if csb[0]:
                func += ir.CSB_Write(csb[1], csb[2])
            else:
                func += While(CSB_Read(csb[1]) != 1)
    return func


#####################################################################
@Tasks.Register("tb.nn.norm.ohbm", ohbm_accel.OHBM)
def Norm(args, output, attrs):
    dtensor, wtensor = args[0], args[1]
    dshape, wshape = dtensor.shape, wtensor.shape
    daddr = dtensor.static_address
    waddr = wtensor.static_address
    oaddr = output[0].static_address
    macro_define = {
        "last_token" : attrs.get("last_token", 0),
        "Token" : dshape[-2] + attrs.get("last_token", 0),
        "Width_in" : dshape[-1],
        "RMS_Norm" : 1 if attrs.get("rms") else 0,
        # "DAT_IN_BASE_ADDR" : daddr,
        # "HBM_WT_BASE_ADDR" : waddr,
        # "DAT_OUT_BASE_ADDR" : oaddr,
    }
    return TestbenchSIM("testbench_LN", macro_define)

@Op.RegisterAttrs("nn.norm", "testbench", ohbm_accel.OHBM)
def tb_nn_norm(args, output, attrs):
    if len(get_vars([args[0].shape, attrs])):
        raise RuntimeError("Unsupport dynamic symbol control in testbench simulation")
    device = args[0].device
    with ir.Function([]) as func:
        csbs = Tasks.Get("tb.nn.norm.ohbm", device)(args, output, attrs)
        for csb in csbs:
            if csb[0]:
                func += ir.CSB_Write(csb[1], csb[2])
            else:
                func += While(CSB_Read(csb[1]) != 1)
    return func


#####################################################################
@Tasks.Register("tb.nn.softmax.ohbm", ohbm_accel.OHBM)
def Softmax(args, output, attrs):
    dtensor = args[0]
    dshape = dtensor.shape
    daddr = dtensor.static_address
    oaddr = output[0].static_address
    macro_define = {
        "last_token" : attrs.get("last_token", 0),
        "Token" : dshape[-2] + attrs.get("last_token", 0),
        "Width_in" : dshape[-1],
        "Feature_Head" : dshape[0],
        "Need_Mask" : 1 if attrs.get("mask") else 0,
        # "DAT_IN_BASE_ADDR" : daddr,
        # "HBM_WT_BASE_ADDR" : waddr,
        # "DAT_OUT_BASE_ADDR" : oaddr,
    }
    return TestbenchSIM("testbench_SOFTMAX", macro_define)

@Op.RegisterAttrs("nn.softmax", "testbench", ohbm_accel.OHBM)
def tb_nn_softmax(args, output, attrs):
    if len(get_vars([args[0].shape, attrs])):
        raise RuntimeError("Unsupport dynamic symbol control in testbench simulation")
    device = args[0].device
    with ir.Function([]) as func:
        csbs = Tasks.Get("tb.nn.softmax.ohbm", device)(args, output, attrs)
        for csb in csbs:
            if csb[0]:
                func += ir.CSB_Write(csb[1], csb[2])
            else:
                func += While(CSB_Read(csb[1]) != 1)
    return func


#####################################################################
@Tasks.Register("tb.nn.elementwise.ohbm", ohbm_accel.OHBM)
def Elementwise(args, output, attrs):
    dtensor, wtensor = args[0], args[1]
    dshape, wshape = dtensor.shape, wtensor.shape
    daddr = dtensor.static_address
    waddr = wtensor.static_address
    oaddr = output[0].static_address
    macro_define = {
        "last_token" : attrs.get("last_token", 0),
        "Token" : dshape[-2] + attrs.get("last_token", 0),
        "Width_in" : dshape[-1],
        "Feature_Head" : dshape[0],
        "ElementWise_Mode" : attrs.get("mode"),
        # "DAT_IN_BASE_ADDR" : daddr,
        # "HBM_WT_BASE_ADDR" : waddr,
        # "DAT_OUT_BASE_ADDR" : oaddr,
    }
    return TestbenchSIM("testbench_ElementWise", macro_define)

@Op.RegisterAttrs("nn.elementwise", "testbench", ohbm_accel.OHBM)
def tb_nn_elementwise(args, output, attrs):
    if len(get_vars([args[0].shape, attrs])):
        raise RuntimeError("Unsupport dynamic symbol control in testbench simulation")
    device = args[0].device
    with ir.Function([]) as func:
        csbs = Tasks.Get("tb.nn.elementwise.ohbm", device)(args, output, attrs)
        for csb in csbs:
            if csb[0]:
                func += ir.CSB_Write(csb[1], csb[2])
            else:
                func += While(CSB_Read(csb[1]) != 1)
    return func


#####################################################################
@Tasks.Register("tb.nn.activate.ohbm", ohbm_accel.OHBM)
def Activate(args, output, attrs):
    dtensor, wtensor = args[0], args[1]
    dshape, wshape = dtensor.shape, wtensor.shape
    daddr = dtensor.static_address
    waddr = wtensor.static_address
    oaddr = output[0].static_address
    macro_define = {
        "last_token" : attrs.get("last_token", 0),
        "Token" : dshape[-2] + attrs.get("last_token", 0),
        "Width_in" : dshape[-1],
        # "DAT_IN_BASE_ADDR" : daddr,
        # "HBM_WT_BASE_ADDR" : waddr,
        # "DAT_OUT_BASE_ADDR" : oaddr,
    }
    return TestbenchSIM("testbench_ACT", macro_define)

@Op.RegisterAttrs("nn.activate", "testbench", ohbm_accel.OHBM)
def tb_nn_activate(args, output, attrs):
    if len(get_vars([args[0].shape, attrs])):
        raise RuntimeError("Unsupport dynamic symbol control in testbench simulation")
    device = args[0].device
    with ir.Function([]) as func:
        csbs = Tasks.Get("tb.nn.activate.ohbm", device)(args, output, attrs)
        for csb in csbs:
            if csb[0]:
                func += ir.CSB_Write(csb[1], csb[2])
            else:
                func += While(CSB_Read(csb[1]) != 1)
    return func


#####################################################################
@Tasks.Register("tb.nn.mvm_f16xf16.ohbm", ohbm_accel.OHBM)
def MVMF16xF16(args, output, attrs):
    dtensor, wtensor = args[0], args[1]
    dshape, wshape = dtensor.shape, wtensor.shape
    daddr = dtensor.static_address
    waddr = wtensor.static_address
    oaddr = output[0].static_address
    if attrs.get("w_trp"):
        macro_define = {
            "last_token" : attrs.get("last_token", 0),
            "Token" : dshape[-2] + attrs.get("last_token", 0),
            "Feature_Head" : dshape[0],
            "Weight_Head" : wshape[0],
            # "DAT_IN_BASE_ADDR" : daddr,
            # "HBM_WT_BASE_ADDR" : waddr,
            # "DAT_OUT_BASE_ADDR" : oaddr,
        }
        return TestbenchSIM("testbench_HBM_MVM_afterTRP_input_head_mode", macro_define)
    else:
        macro_define = {
            "last_token" : attrs.get("last_token", 0),
            "Token" : dshape[-2] + attrs.get("last_token", 0),
            "Feature_Head" : dshape[0],
            "Weight_Head" : wshape[0],
            # "DAT_IN_BASE_ADDR" : daddr,
            # "HBM_WT_BASE_ADDR" : waddr,
            # "DAT_OUT_BASE_ADDR" : oaddr,
        }
        return TestbenchSIM("testbench_HBM_MVM_afterF2W_output_head_mode", macro_define)

@Op.RegisterAttrs("nn.mvm_f16xf16", "testbench", ohbm_accel.OHBM)
def tb_nn_mm_f16xf16(args, output, attrs):
    if len(get_vars([args[0].shape, attrs])):
        raise RuntimeError("Unsupport dynamic symbol control in testbench simulation")
    device = args[0].device
    with ir.Function([]) as func:
        csbs = Tasks.Get("tb.nn.mvm_f16xf16.ohbm", device)(args, output, attrs)
        for csb in csbs:
            if csb[0]:
                func += ir.CSB_Write(csb[1], csb[2])
            else:
                func += While(CSB_Read(csb[1]) != 1)
    return func
