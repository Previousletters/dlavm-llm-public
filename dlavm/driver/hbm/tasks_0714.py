import numpy as np
import math
from ..basic import CSB_Read, CSB_Write, CSB_For, CSB_End, Ceil, Ceil_Padding, Tasks
from ...ne import Var, If, For, Numb, expr_for_hook
from ...clib import FP32_to_FP20
from ... import device

__version__ = "HBM0602"

MVM_MODE = 0b00100011111
MVMBN_MODE = 0b01100011111
MVMBNARG_MODE = 0b101100011111
MVMBNRES_MODE = 0b11100011111
MVMBNRESARG_MODE = 0b111100011111


@Tasks.Register("accel.hbm.mvm", device.HBM0603)
def MVMBasic(**kwargs):
    device = kwargs["device"]
    Token = kwargs["Token"]
    kvcache = kwargs.get("kvcache", 0)
    kvcache_offset = kwargs.get("kvcache_offset", 0)
    Head = kwargs.get("Head", 1)
    Padding = kwargs.get("padding", 0)
    Width_in = kwargs["Width_in"]
    Width_out = kwargs["Width_out"]
    EW_MODE = kwargs.get("EW_MODE", 0)
    RELU_EN = kwargs.get("RELU_EN", 0)
    DAT_OUT_LINE_STRIDE = kwargs.get("DAT_OUT_LINE_STRIDE")
    DAT_OUT_SURFACE_STRIDE = kwargs.get("DAT_OUT_SURFACE_STRIDE")
    DAT_IN_BASE_ADDR = kwargs.get("DAT_IN_BASE_ADDR")
    HBM00_WT_BASE_ADDR = kwargs.get("HBM00_WT_BASE_ADDR")
    BN_BASE_ADDR = kwargs.get("BN_BASE_ADDR")
    Res_Add_BASE_ADDR = kwargs.get("Res_Add_BASE_ADDR")
    DAT_OUT_BASE_ADDR = kwargs.get("DAT_OUT_BASE_ADDR")
    AUGMAX_OUT_ADDR = kwargs.get("AUGMAX_OUT_ADDR")
    log2_WT_base_addr_Bank_Step = kwargs.get("log2_WT_base_addr_Bank_Step", 28)
    Skip_Factor = kwargs.get("Skip_Factor", 1)
    last_token = kwargs.get("last_token", 0)
    full = kwargs.get("full", 0)
    DAT_IN_ONCHIP = None
    RES_IN_ONCHIP = None
    DAT_OUT_ONCHIP = None

    if full:
        Tin = device.base_Tin
        Tout = device.Tout
        Pixel_Data_Bytes = device.Pixel_Data_Bytes
        WT_DW = device.MAX_WT_DW
        HBM_AXI_DATA_WIDTH = device.HBM_AXI_DATA_WIDTH
        WT_CH_Tgroup = device.WT_CH_Tgroup
        MAX_WT_DW = device.MAX_WT_DW
        MAX_BN_DW = device.MAX_BN_DW
        HBM_Port = device.HBM_Port
        WT_BRAM_DEPTH = device.WT_BRAM_DEPTH
        AXI_BN_WIDTH = device.AXI_BN_WIDTH
        BN_FIFO_DEP = device.BN_FIFO_DEP
        BN_FIFO_NUM = device.BN_FIFO_NUM
        MAX_TOKEN = device.MAX_TOKEN

        real_Token = Token - last_token
        split_token = 64
        for_k = Var("for_k")
        Win = If(kvcache, 1, If((real_Token) > split_token, If(for_k < Ceil(real_Token, split_token)-1, split_token, real_Token%split_token), real_Token))
        Hin = Head
        CHin = Width_in
        CHout = Width_out
        Wout = Win
        Hout = Hin
        CHout_div_Tout = ((CHout + Tout - 1) // Tout)
        CHin_div_Tout = ((CHin + Tout - 1) // Tout)
        CHin_Padding_with_Tout = CHin_div_Tout * Tout
        Tin_div_Tout = (Tin + Tout - 1) // Tout
        CHout_Padding = CHout_div_Tout * Tout
        DAT_IN_LINE_STRIDE = Pixel_Data_Bytes * If(kvcache, 1, real_Token)
        DAT_IN_SURFACE_STRIDE = Pixel_Data_Bytes * If(kvcache, 1, real_Token) * Hin
        if DAT_OUT_LINE_STRIDE is None or DAT_OUT_SURFACE_STRIDE is None:
            DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes * If(kvcache, 1, real_Token)
            DAT_OUT_SURFACE_STRIDE = Pixel_Data_Bytes * If(kvcache, 1, real_Token) * Hout

        WT_CHin_div_Tin = (CHin + Tin - 1) // Tin
        WT_CHin_Padding_with_Tin = WT_CHin_div_Tin*Tin
        WT_scale_group_nums = (WT_CHin_Padding_with_Tin + WT_CH_Tgroup-1) // WT_CH_Tgroup
        WT_scale_bits = CHout_Padding*HBM_AXI_DATA_WIDTH*WT_scale_group_nums
        WT_SIZE_IN_BYTES = (((CHout_Padding*WT_CHin_Padding_with_Tin*WT_DW)>>3)+((WT_scale_bits)>>3))

        if AUGMAX_OUT_ADDR is not None:
            if Res_Add_BASE_ADDR is not None:
                mode = MVMBNRESARG_MODE
            elif BN_BASE_ADDR is not None:
                mode = MVMBNARG_MODE
                Res_Add_BASE_ADDR = 0
            else:
                raise RuntimeError("MVM does NOT support ArgMax")
        elif Res_Add_BASE_ADDR is not None:
            mode = MVMBNRES_MODE
        elif BN_BASE_ADDR is not None:
            mode = MVMBN_MODE
            Res_Add_BASE_ADDR = 0
        else:
            mode = MVM_MODE
            BN_BASE_ADDR = 0
            Res_Add_BASE_ADDR = 0
        
        ## Hardware Testbench
        CHin = CHin_Padding_with_Tout
        CHout = CHout_Padding
        dat_num_per_row=Win*WT_CHin_div_Tin
        min_dat_depth=dat_num_per_row
        min_wt_depth=WT_CHin_div_Tin*((Tin*MAX_WT_DW)//HBM_AXI_DATA_WIDTH)*(Tout//HBM_Port)

        out_ch_slice=((WT_BRAM_DEPTH*2)//min_wt_depth)*Tout
        BN_FIFO_bits=AXI_BN_WIDTH*BN_FIFO_DEP*BN_FIFO_NUM
        BN_FIFO_chout_num=BN_FIFO_bits//(MAX_BN_DW*2)

        '''
        if out_ch_slice>BN_FIFO_chout_num and mode != MVM_MODE:
            out_ch_slice=BN_FIFO_chout_num

        if out_ch_slice>=CHout_Padding:
            out_ch_slice=CHout_Padding
            CHout_Split_Times=1
        else:
            CHout_Split_Times=(CHout_Padding+out_ch_slice-1)//out_ch_slice

        if CHout%out_ch_slice==0:
            out_ch_slice_last=out_ch_slice
        else:
            out_ch_slice_last=CHout_Padding%out_ch_slice
        '''
        if mode != MVM_MODE:
            out_ch_slice = If(out_ch_slice>BN_FIFO_chout_num, BN_FIFO_chout_num, out_ch_slice)
        out_ch_slice = If(out_ch_slice >= CHout_Padding, CHout_Padding, out_ch_slice)
        CHout_Split_Times = If(out_ch_slice >= CHout_Padding, 1, (CHout_Padding+out_ch_slice-1)//out_ch_slice)
        out_ch_slice_last = If(CHout%out_ch_slice, CHout_Padding%out_ch_slice, out_ch_slice)

        CHout_Split_Times_minus1=CHout_Split_Times-1
        wt_size_in_bits = WT_SIZE_IN_BYTES // CHout_Padding * 8
        CHout = out_ch_slice
        CHout_last = out_ch_slice_last

        if Padding:
            feature_out_base = If(kvcache, DAT_OUT_BASE_ADDR + (Token-1)*Pixel_Data_Bytes, DAT_OUT_BASE_ADDR + last_token*Pixel_Data_Bytes)
            DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes*MAX_TOKEN
            DAT_OUT_SURFACE_STRIDE = Pixel_Data_Bytes*MAX_TOKEN*Hout
        else:
            DAT_IN_BASE_ADDR = If(kvcache_offset, DAT_IN_BASE_ADDR + (real_Token-1)*Pixel_Data_Bytes, DAT_IN_BASE_ADDR)
            DAT_IN_LINE_STRIDE = If(kvcache_offset, Pixel_Data_Bytes * Win, DAT_IN_LINE_STRIDE)
            DAT_IN_SURFACE_STRIDE = If(kvcache_offset, Pixel_Data_Bytes * Win * Hin, DAT_IN_SURFACE_STRIDE)
            feature_out_base = DAT_OUT_BASE_ADDR

        regs = []
        @expr_for_hook(CSB_For, CSB_End)
        def for_mvm_single(k):
            DAT_IN_ADDR = DAT_IN_BASE_ADDR + k * split_token * Pixel_Data_Bytes
            RES_IN_ADDR = Res_Add_BASE_ADDR + If(mode==MVMBNRES_MODE or mode==MVMBNRESARG_MODE, k * split_token * Pixel_Data_Bytes, 0)
            DAT_OUT_ADDR = feature_out_base + k * split_token * Pixel_Data_Bytes
            onchip = 0
            if DAT_IN_ONCHIP is not None:
                onchip += 0b1
                DAT_IN_ADDR = If(kvcache, DAT_IN_ONCHIP, DAT_IN_ADDR)
            if RES_IN_ONCHIP is not None:
                RES_IN_ADDR = If(kvcache, RES_IN_ONCHIP, RES_IN_ADDR)
            if DAT_OUT_ONCHIP is not None:
                onchip += 0b10
                DAT_OUT_ADDR = If(kvcache, DAT_OUT_ONCHIP, DAT_OUT_ADDR)
            CSB_Write(regs, 2,CHin)
            CSB_Write(regs, 3,Win)
            CSB_Write(regs, 4,Hin)
            CSB_Write(regs, 5,Wout)
            CSB_Write(regs, 6,Hout)
            CSB_Write(regs, 7,CHout)
            CSB_Write(regs, 8,CHout_last)
            CSB_Write(regs, 9,Win)
            
            CSB_Write(regs, 10,DAT_IN_ADDR)
            CSB_Write(regs, 11,HBM00_WT_BASE_ADDR)
            CSB_Write(regs, 12,wt_size_in_bits)
            CSB_Write(regs, 13,DAT_OUT_ADDR)
            CSB_Write(regs, 14,CHout_Split_Times_minus1)
            CSB_Write(regs, 15,log2_WT_base_addr_Bank_Step)
            CSB_Write(regs, 16,(EW_MODE << 1) + RELU_EN)
            if AUGMAX_OUT_ADDR is not None:
                CSB_Write(regs, 17,AUGMAX_OUT_ADDR)
            else:
                CSB_Write(regs, 17,Skip_Factor-1)
            CSB_Write(regs, 18,onchip)
            
            CSB_Write(regs, 19,0)
            CSB_Write(regs, 20,0)
            CSB_Write(regs, 21,1)
            CSB_Write(regs, 22,1)
            CSB_Write(regs, 23,1)
            CSB_Write(regs, 24,1)
            CSB_Write(regs, 25,0)
            CSB_Write(regs, 26,BN_BASE_ADDR)
            CSB_Write(regs, 27,RES_IN_ADDR)
            CSB_Write(regs, 28,0)
            CSB_Write(regs, 29,DAT_IN_SURFACE_STRIDE)
            CSB_Write(regs, 30,DAT_IN_LINE_STRIDE)
            CSB_Write(regs, 31,DAT_OUT_SURFACE_STRIDE)
            CSB_Write(regs, 32,DAT_OUT_LINE_STRIDE)
            CSB_Write(regs, 33,mode)
            
            if AUGMAX_OUT_ADDR is not None:
                CSB_Read(regs, 40, 1)
            else:
                CSB_Read(regs, 1, 1)
        for_expr_k = For(for_k, Numb(0), If(kvcache, 1, Ceil(real_Token, split_token)), for_mvm_single)
        for_expr_k.run(regs)
        return regs
    else:
        Tin = device.base_Tin
        Tout = device.Tout
        Pixel_Data_Bytes = device.Pixel_Data_Bytes
        WT_DW = device.MAX_WT_DW
        HBM_AXI_DATA_WIDTH = device.HBM_AXI_DATA_WIDTH
        WT_CH_Tgroup = device.WT_CH_Tgroup
        MAX_WT_DW = device.MAX_WT_DW
        MAX_BN_DW = device.MAX_BN_DW
        HBM_Port = device.HBM_Port
        WT_BRAM_DEPTH = device.WT_BRAM_DEPTH
        AXI_BN_WIDTH = device.AXI_BN_WIDTH
        BN_FIFO_DEP = device.BN_FIFO_DEP
        BN_FIFO_NUM = device.BN_FIFO_NUM
        MAX_TOKEN = device.MAX_TOKEN

        Win = If(kvcache, 1, Token - last_token)
        Hin = Head
        CHin = Width_in
        CHout = Width_out
        Wout = Win
        Hout = Hin
        CHout_div_Tout = ((CHout + Tout - 1) // Tout)
        CHin_div_Tout = ((CHin + Tout - 1) // Tout)
        CHin_Padding_with_Tout = CHin_div_Tout * Tout
        Tin_div_Tout = (Tin + Tout - 1) // Tout
        CHout_Padding = CHout_div_Tout * Tout
        DAT_IN_LINE_STRIDE = Pixel_Data_Bytes * Win
        DAT_IN_SURFACE_STRIDE = Pixel_Data_Bytes * Win * Hin
        if DAT_OUT_LINE_STRIDE is None or DAT_OUT_SURFACE_STRIDE is None:
            DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes * Wout
            DAT_OUT_SURFACE_STRIDE = Pixel_Data_Bytes * Wout * Hout

        WT_CHin_div_Tin = (CHin + Tin - 1) // Tin
        WT_CHin_Padding_with_Tin = WT_CHin_div_Tin*Tin
        WT_scale_group_nums = (WT_CHin_Padding_with_Tin + WT_CH_Tgroup-1) // WT_CH_Tgroup
        WT_scale_bits = CHout_Padding*HBM_AXI_DATA_WIDTH*WT_scale_group_nums
        WT_SIZE_IN_BYTES = (((CHout_Padding*WT_CHin_Padding_with_Tin*WT_DW)>>3)+((WT_scale_bits)>>3))

        if AUGMAX_OUT_ADDR is not None:
            if Res_Add_BASE_ADDR is not None:
                mode = MVMBNRESARG_MODE
            elif BN_BASE_ADDR is not None:
                mode = MVMBNARG_MODE
                Res_Add_BASE_ADDR = 0
            else:
                raise RuntimeError("MVM does NOT support ArgMax")
        elif Res_Add_BASE_ADDR is not None:
            mode = MVMBNRES_MODE
        elif BN_BASE_ADDR is not None:
            mode = MVMBN_MODE
            Res_Add_BASE_ADDR = 0
        else:
            mode = MVM_MODE
            BN_BASE_ADDR = 0
            Res_Add_BASE_ADDR = 0
        
        ## Hardware Testbench
        CHin = CHin_Padding_with_Tout
        CHout = CHout_Padding
        dat_num_per_row=Win*WT_CHin_div_Tin
        min_dat_depth=dat_num_per_row
        min_wt_depth=WT_CHin_div_Tin*((Tin*MAX_WT_DW)//HBM_AXI_DATA_WIDTH)*(Tout//HBM_Port)

        out_ch_slice=((WT_BRAM_DEPTH*2)//min_wt_depth)*Tout
        BN_FIFO_bits=AXI_BN_WIDTH*BN_FIFO_DEP*BN_FIFO_NUM
        BN_FIFO_chout_num=BN_FIFO_bits//(MAX_BN_DW*2)

        '''
        if out_ch_slice>BN_FIFO_chout_num and mode != MVM_MODE:
            out_ch_slice=BN_FIFO_chout_num

        if out_ch_slice>=CHout_Padding:
            out_ch_slice=CHout_Padding
            CHout_Split_Times=1
        else:
            CHout_Split_Times=(CHout_Padding+out_ch_slice-1)//out_ch_slice

        if CHout%out_ch_slice==0:
            out_ch_slice_last=out_ch_slice
        else:
            out_ch_slice_last=CHout_Padding%out_ch_slice
        '''
        if mode != MVM_MODE:
            out_ch_slice = If(out_ch_slice>BN_FIFO_chout_num, BN_FIFO_chout_num, out_ch_slice)
        out_ch_slice = If(out_ch_slice >= CHout_Padding, CHout_Padding, out_ch_slice)
        CHout_Split_Times = If(out_ch_slice >= CHout_Padding, 1, (CHout_Padding+out_ch_slice-1)//out_ch_slice)
        out_ch_slice_last = If(CHout%out_ch_slice, CHout_Padding%out_ch_slice, out_ch_slice)

        CHout_Split_Times_minus1=CHout_Split_Times-1
        wt_size_in_bits = WT_SIZE_IN_BYTES // CHout_Padding * 8
        CHout = out_ch_slice
        CHout_last = out_ch_slice_last

        if Padding:
            feature_out_base = If(kvcache, DAT_OUT_BASE_ADDR + (Token-1)*Pixel_Data_Bytes, DAT_OUT_BASE_ADDR + last_token*Pixel_Data_Bytes)
            DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes*MAX_TOKEN
            DAT_OUT_SURFACE_STRIDE = Pixel_Data_Bytes*MAX_TOKEN*Hout
        else:
            DAT_IN_BASE_ADDR = If(kvcache_offset, DAT_IN_BASE_ADDR + (Token-1)*Pixel_Data_Bytes, DAT_IN_BASE_ADDR)
            DAT_IN_LINE_STRIDE = If(kvcache_offset, Pixel_Data_Bytes * Win, DAT_IN_LINE_STRIDE)
            DAT_IN_SURFACE_STRIDE = If(kvcache_offset, Pixel_Data_Bytes * Win * Hin, DAT_IN_SURFACE_STRIDE)
            feature_out_base = DAT_OUT_BASE_ADDR

        onchip = 0
        if DAT_IN_ONCHIP is not None:
            onchip += 0b1
            DAT_IN_BASE_ADDR = If(kvcache, DAT_IN_ONCHIP, DAT_IN_BASE_ADDR)
        if RES_IN_ONCHIP is not None:
            Res_Add_BASE_ADDR = If(kvcache, RES_IN_ONCHIP, Res_Add_BASE_ADDR)
        if DAT_OUT_ONCHIP is not None:
            onchip += 0b10
            feature_out_base = If(kvcache, DAT_OUT_ONCHIP, feature_out_base)
        regs = []
        CSB_Write(regs, 2,CHin)
        CSB_Write(regs, 3,Win)
        CSB_Write(regs, 4,Hin)
        CSB_Write(regs, 5,Wout)
        CSB_Write(regs, 6,Hout)
        CSB_Write(regs, 7,CHout)
        CSB_Write(regs, 8,CHout_last)
        CSB_Write(regs, 9,If(kvcache, 1, (Token - last_token)))
        
        CSB_Write(regs, 10,DAT_IN_BASE_ADDR)
        CSB_Write(regs, 11,HBM00_WT_BASE_ADDR)
        CSB_Write(regs, 12,wt_size_in_bits)
        CSB_Write(regs, 13,feature_out_base)
        CSB_Write(regs, 14,CHout_Split_Times_minus1)
        CSB_Write(regs, 15,log2_WT_base_addr_Bank_Step)
        CSB_Write(regs, 16,(EW_MODE << 1) + RELU_EN)
        if AUGMAX_OUT_ADDR is not None:
            CSB_Write(regs, 17,AUGMAX_OUT_ADDR)
        else:
            CSB_Write(regs, 17,Skip_Factor-1)
        CSB_Write(regs, 18,onchip)
        
        CSB_Write(regs, 19,0)
        CSB_Write(regs, 20,0)
        CSB_Write(regs, 21,1)
        CSB_Write(regs, 22,1)
        CSB_Write(regs, 23,1)
        CSB_Write(regs, 24,1)
        CSB_Write(regs, 25,0)
        CSB_Write(regs, 26,BN_BASE_ADDR)
        CSB_Write(regs, 27,Res_Add_BASE_ADDR)
        CSB_Write(regs, 28,0)
        CSB_Write(regs, 29,DAT_IN_SURFACE_STRIDE)
        CSB_Write(regs, 30,DAT_IN_LINE_STRIDE)
        CSB_Write(regs, 31,DAT_OUT_SURFACE_STRIDE)
        CSB_Write(regs, 32,DAT_OUT_LINE_STRIDE)
        CSB_Write(regs, 33,mode)
        
        if AUGMAX_OUT_ADDR is not None:
            CSB_Read(regs, 40, 1)
        else:
            CSB_Read(regs, 1, 1)
        return regs


@Tasks.Register("accel.hbm.mvm", device.EdgeLLMv2)
def MVMBasic_v2(**kwargs):
    device = kwargs["device"]
    Token = kwargs["Token"]
    kvcache = kwargs.get("kvcache", 0)
    kvcache_offset = kwargs.get("kvcache_offset", 0)
    Head = kwargs.get("Head", 1)
    Padding = kwargs.get("padding", 0)
    Width_in = kwargs["Width_in"]
    Width_out = kwargs["Width_out"]
    EW_MODE = kwargs.get("EW_MODE", 0)
    RELU_EN = kwargs.get("RELU_EN", 0)
    DAT_OUT_LINE_STRIDE = kwargs.get("DAT_OUT_LINE_STRIDE")
    DAT_OUT_SURFACE_STRIDE = kwargs.get("DAT_OUT_SURFACE_STRIDE")
    DAT_IN_BASE_ADDR = kwargs.get("DAT_IN_BASE_ADDR")
    HBM00_WT_BASE_ADDR = kwargs.get("HBM00_WT_BASE_ADDR")
    BN_BASE_ADDR = kwargs.get("BN_BASE_ADDR")
    Res_Add_BASE_ADDR = kwargs.get("Res_Add_BASE_ADDR")
    DAT_OUT_BASE_ADDR = kwargs.get("DAT_OUT_BASE_ADDR")
    AUGMAX_OUT_ADDR = kwargs.get("AUGMAX_OUT_ADDR")
    log2_WT_base_addr_Bank_Step = kwargs.get("log2_WT_base_addr_Bank_Step", 28)
    Skip_Factor = kwargs.get("Skip_Factor", 1)
    last_token = kwargs.get("last_token", 0)
    full = kwargs.get("full", 0)
    DAT_IN_ONCHIP = kwargs.get("DAT_IN_ONCHIP")
    RES_IN_ONCHIP = kwargs.get("RES_IN_ONCHIP")
    DAT_OUT_ONCHIP = kwargs.get("DAT_OUT_ONCHIP")
    reg_22 = (device.log2_WT_CH_Tgroup << device.log2_CH) + device.WT_CH_Tgroup
    reg_23 = (device.log2_T_quant_block << device.log2_CH) + device.T_quant_block

    if full:
        Tin = device.base_Tin
        Tout = device.Tout
        Pixel_Data_Bytes = device.Pixel_Data_Bytes
        WT_DW = device.MAX_WT_DW
        HBM_AXI_DATA_WIDTH = device.HBM_AXI_DATA_WIDTH
        WT_CH_Tgroup = device.WT_CH_Tgroup
        MAX_WT_DW = device.MAX_WT_DW
        MAX_BN_DW = device.MAX_BN_DW
        HBM_Port = device.HBM_Port
        WT_BRAM_DEPTH = device.WT_BRAM_DEPTH
        AXI_BN_WIDTH = device.AXI_BN_WIDTH
        BN_FIFO_DEP = device.BN_FIFO_DEP
        BN_FIFO_NUM = device.BN_FIFO_NUM
        MAX_TOKEN = device.MAX_TOKEN

        real_Token = Token - last_token
        split_token = 64
        for_k = Var("for_k")
        Win = If(kvcache, 1, If((real_Token) > split_token, If(for_k < Ceil(real_Token, split_token)-1, split_token, real_Token%split_token), real_Token))
        Hin = Head
        CHin = Width_in
        CHout = Width_out
        Wout = Win
        Hout = Hin
        CHout_div_Tout = ((CHout + Tout - 1) // Tout)
        CHin_div_Tout = ((CHin + Tout - 1) // Tout)
        CHin_Padding_with_Tout = CHin_div_Tout * Tout
        Tin_div_Tout = (Tin + Tout - 1) // Tout
        CHout_Padding = CHout_div_Tout * Tout
        DAT_IN_LINE_STRIDE = Pixel_Data_Bytes * If(kvcache, 1, real_Token)
        DAT_IN_SURFACE_STRIDE = Pixel_Data_Bytes * If(kvcache, 1, real_Token) * Hin
        if DAT_OUT_LINE_STRIDE is None or DAT_OUT_SURFACE_STRIDE is None:
            DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes * If(kvcache, 1, real_Token)
            DAT_OUT_SURFACE_STRIDE = Pixel_Data_Bytes * If(kvcache, 1, real_Token) * Hout

        WT_CHin_div_Tin = (CHin + Tin - 1) // Tin
        WT_CHin_Padding_with_Tin = WT_CHin_div_Tin*Tin
        WT_scale_group_nums = (WT_CHin_Padding_with_Tin + WT_CH_Tgroup-1) // WT_CH_Tgroup
        WT_scale_bits = CHout_Padding*HBM_AXI_DATA_WIDTH*WT_scale_group_nums
        WT_SIZE_IN_BYTES = (((CHout_Padding*WT_CHin_Padding_with_Tin*WT_DW)>>3)+((WT_scale_bits)>>3))

        if AUGMAX_OUT_ADDR is not None:
            if Res_Add_BASE_ADDR is not None:
                mode = MVMBNRESARG_MODE
            elif BN_BASE_ADDR is not None:
                mode = MVMBNARG_MODE
                Res_Add_BASE_ADDR = 0
            else:
                raise RuntimeError("MVM does NOT support ArgMax")
        elif Res_Add_BASE_ADDR is not None:
            mode = MVMBNRES_MODE
        elif BN_BASE_ADDR is not None:
            mode = MVMBN_MODE
            Res_Add_BASE_ADDR = 0
        else:
            mode = MVM_MODE
            BN_BASE_ADDR = 0
            Res_Add_BASE_ADDR = 0
        
        ## Hardware Testbench
        CHin = CHin_Padding_with_Tout
        CHout = CHout_Padding
        dat_num_per_row=Win*WT_CHin_div_Tin
        min_dat_depth=dat_num_per_row
        min_wt_depth=WT_CHin_div_Tin*((Tin*MAX_WT_DW)//HBM_AXI_DATA_WIDTH)*(Tout//HBM_Port)

        out_ch_slice=((WT_BRAM_DEPTH*2)//min_wt_depth)*Tout
        BN_FIFO_bits=AXI_BN_WIDTH*BN_FIFO_DEP*BN_FIFO_NUM
        BN_FIFO_chout_num=BN_FIFO_bits//(MAX_BN_DW*2)

        '''
        if out_ch_slice>BN_FIFO_chout_num and mode != MVM_MODE:
            out_ch_slice=BN_FIFO_chout_num

        if out_ch_slice>=CHout_Padding:
            out_ch_slice=CHout_Padding
            CHout_Split_Times=1
        else:
            CHout_Split_Times=(CHout_Padding+out_ch_slice-1)//out_ch_slice

        if CHout%out_ch_slice==0:
            out_ch_slice_last=out_ch_slice
        else:
            out_ch_slice_last=CHout_Padding%out_ch_slice
        '''
        if mode != MVM_MODE:
            out_ch_slice = If(out_ch_slice>BN_FIFO_chout_num, BN_FIFO_chout_num, out_ch_slice)
        out_ch_slice = If(out_ch_slice >= CHout_Padding, CHout_Padding, out_ch_slice)
        CHout_Split_Times = If(out_ch_slice >= CHout_Padding, 1, (CHout_Padding+out_ch_slice-1)//out_ch_slice)
        out_ch_slice_last = If(CHout%out_ch_slice, CHout_Padding%out_ch_slice, out_ch_slice)

        CHout_Split_Times_minus1=CHout_Split_Times-1
        wt_size_in_bits = WT_SIZE_IN_BYTES // CHout_Padding * 8
        CHout = out_ch_slice
        CHout_last = out_ch_slice_last
        Last_Group_CHin = If(WT_CHin_Padding_with_Tin%WT_CH_Tgroup, WT_CHin_Padding_with_Tin%WT_CH_Tgroup, WT_CH_Tgroup)

        if Padding:
            feature_out_base = If(kvcache, DAT_OUT_BASE_ADDR + (Token-1)*Pixel_Data_Bytes, DAT_OUT_BASE_ADDR + last_token*Pixel_Data_Bytes)
            DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes*MAX_TOKEN
            DAT_OUT_SURFACE_STRIDE = Pixel_Data_Bytes*MAX_TOKEN*Hout
        else:
            DAT_IN_BASE_ADDR = If(kvcache_offset, DAT_IN_BASE_ADDR + (real_Token-1)*Pixel_Data_Bytes, DAT_IN_BASE_ADDR)
            DAT_IN_LINE_STRIDE = If(kvcache_offset, Pixel_Data_Bytes * Win, DAT_IN_LINE_STRIDE)
            DAT_IN_SURFACE_STRIDE = If(kvcache_offset, Pixel_Data_Bytes * Win * Hin, DAT_IN_SURFACE_STRIDE)
            feature_out_base = DAT_OUT_BASE_ADDR

        regs = []
        @expr_for_hook(CSB_For, CSB_End)
        def for_mvm_single(k):
            DAT_IN_ADDR = DAT_IN_BASE_ADDR + k * split_token * Pixel_Data_Bytes
            RES_IN_ADDR = Res_Add_BASE_ADDR + If(mode==MVMBNRES_MODE or mode==MVMBNRESARG_MODE, k * split_token * Pixel_Data_Bytes, 0)
            DAT_OUT_ADDR = feature_out_base + k * split_token * Pixel_Data_Bytes
            onchip = 0
            if DAT_IN_ONCHIP is not None:
                onchip += 0b1
                DAT_IN_ADDR = If(kvcache, DAT_IN_ONCHIP, DAT_IN_ADDR)
            if RES_IN_ONCHIP is not None:
                RES_IN_ADDR = If(kvcache, RES_IN_ONCHIP, RES_IN_ADDR)
            if DAT_OUT_ONCHIP is not None:
                onchip += 0b10
                DAT_OUT_ADDR = If(kvcache, DAT_OUT_ONCHIP, DAT_OUT_ADDR)
            if onchip:
                onchip = If(kvcache, onchip, 0)
            CSB_Write(regs, 2,CHin)
            CSB_Write(regs, 3,Win)
            CSB_Write(regs, 4,Hin)
            CSB_Write(regs, 5,Wout)
            CSB_Write(regs, 6,Hout)
            CSB_Write(regs, 7,CHout)
            CSB_Write(regs, 8,CHout_last)
            CSB_Write(regs, 9,Win)
            
            CSB_Write(regs, 10,DAT_IN_ADDR)
            CSB_Write(regs, 11,HBM00_WT_BASE_ADDR)
            CSB_Write(regs, 12,wt_size_in_bits)
            CSB_Write(regs, 13,DAT_OUT_ADDR)
            CSB_Write(regs, 14,CHout_Split_Times_minus1)
            CSB_Write(regs, 15,log2_WT_base_addr_Bank_Step)
            CSB_Write(regs, 16,(EW_MODE << 1) + RELU_EN)
            if AUGMAX_OUT_ADDR is not None:
                CSB_Write(regs, 17,AUGMAX_OUT_ADDR)
            else:
                CSB_Write(regs, 17,Skip_Factor-1)
            CSB_Write(regs, 18,onchip)
            
            CSB_Write(regs, 19,0)
            CSB_Write(regs, 20,0)
            CSB_Write(regs, 21,0)
            CSB_Write(regs, 22,reg_22)
            CSB_Write(regs, 23,reg_23)
            CSB_Write(regs, 24,Last_Group_CHin)
            CSB_Write(regs, 25,0)
            CSB_Write(regs, 26,BN_BASE_ADDR)
            CSB_Write(regs, 27,RES_IN_ADDR)
            CSB_Write(regs, 28,0)
            CSB_Write(regs, 29,DAT_IN_SURFACE_STRIDE)
            CSB_Write(regs, 30,DAT_IN_LINE_STRIDE)
            CSB_Write(regs, 31,DAT_OUT_SURFACE_STRIDE)
            CSB_Write(regs, 32,DAT_OUT_LINE_STRIDE)
            CSB_Write(regs, 33,mode)
            
            if AUGMAX_OUT_ADDR is not None:
                CSB_Read(regs, 40, 1)
            else:
                CSB_Read(regs, 1, 1)
        for_expr_k = For(for_k, Numb(0), If(kvcache, 1, Ceil(real_Token, split_token)), for_mvm_single)
        for_expr_k.run(regs)
        return regs
    else:
        Tin = device.base_Tin
        Tout = device.Tout
        Pixel_Data_Bytes = device.Pixel_Data_Bytes
        WT_DW = device.MAX_WT_DW
        HBM_AXI_DATA_WIDTH = device.HBM_AXI_DATA_WIDTH
        WT_CH_Tgroup = device.WT_CH_Tgroup
        MAX_WT_DW = device.MAX_WT_DW
        MAX_BN_DW = device.MAX_BN_DW
        HBM_Port = device.HBM_Port
        WT_BRAM_DEPTH = device.WT_BRAM_DEPTH
        AXI_BN_WIDTH = device.AXI_BN_WIDTH
        BN_FIFO_DEP = device.BN_FIFO_DEP
        BN_FIFO_NUM = device.BN_FIFO_NUM
        MAX_TOKEN = device.MAX_TOKEN

        Win = If(kvcache, 1, Token - last_token)
        Hin = Head
        CHin = Width_in
        CHout = Width_out
        Wout = Win
        Hout = Hin
        CHout_div_Tout = ((CHout + Tout - 1) // Tout)
        CHin_div_Tout = ((CHin + Tout - 1) // Tout)
        CHin_Padding_with_Tout = CHin_div_Tout * Tout
        Tin_div_Tout = (Tin + Tout - 1) // Tout
        CHout_Padding = CHout_div_Tout * Tout
        DAT_IN_LINE_STRIDE = Pixel_Data_Bytes * Win
        DAT_IN_SURFACE_STRIDE = Pixel_Data_Bytes * Win * Hin
        if DAT_OUT_LINE_STRIDE is None or DAT_OUT_SURFACE_STRIDE is None:
            DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes * Wout
            DAT_OUT_SURFACE_STRIDE = Pixel_Data_Bytes * Wout * Hout

        WT_CHin_div_Tin = (CHin + Tin - 1) // Tin
        WT_CHin_Padding_with_Tin = WT_CHin_div_Tin*Tin
        WT_scale_group_nums = (WT_CHin_Padding_with_Tin + WT_CH_Tgroup-1) // WT_CH_Tgroup
        WT_scale_bits = CHout_Padding*HBM_AXI_DATA_WIDTH*WT_scale_group_nums
        WT_SIZE_IN_BYTES = (((CHout_Padding*WT_CHin_Padding_with_Tin*WT_DW)>>3)+((WT_scale_bits)>>3))

        if AUGMAX_OUT_ADDR is not None:
            if Res_Add_BASE_ADDR is not None:
                mode = MVMBNRESARG_MODE
            elif BN_BASE_ADDR is not None:
                mode = MVMBNARG_MODE
                Res_Add_BASE_ADDR = 0
            else:
                raise RuntimeError("MVM does NOT support ArgMax")
        elif Res_Add_BASE_ADDR is not None:
            mode = MVMBNRES_MODE
        elif BN_BASE_ADDR is not None:
            mode = MVMBN_MODE
            Res_Add_BASE_ADDR = 0
        else:
            mode = MVM_MODE
            BN_BASE_ADDR = 0
            Res_Add_BASE_ADDR = 0
        
        ## Hardware Testbench
        CHin = CHin_Padding_with_Tout
        CHout = CHout_Padding
        dat_num_per_row=Win*WT_CHin_div_Tin
        min_dat_depth=dat_num_per_row
        min_wt_depth=WT_CHin_div_Tin*((Tin*MAX_WT_DW)//HBM_AXI_DATA_WIDTH)*(Tout//HBM_Port)

        out_ch_slice=((WT_BRAM_DEPTH*2)//min_wt_depth)*Tout
        BN_FIFO_bits=AXI_BN_WIDTH*BN_FIFO_DEP*BN_FIFO_NUM
        BN_FIFO_chout_num=BN_FIFO_bits//(MAX_BN_DW*2)

        '''
        if out_ch_slice>BN_FIFO_chout_num and mode != MVM_MODE:
            out_ch_slice=BN_FIFO_chout_num

        if out_ch_slice>=CHout_Padding:
            out_ch_slice=CHout_Padding
            CHout_Split_Times=1
        else:
            CHout_Split_Times=(CHout_Padding+out_ch_slice-1)//out_ch_slice

        if CHout%out_ch_slice==0:
            out_ch_slice_last=out_ch_slice
        else:
            out_ch_slice_last=CHout_Padding%out_ch_slice
        '''
        if mode != MVM_MODE:
            out_ch_slice = If(out_ch_slice>BN_FIFO_chout_num, BN_FIFO_chout_num, out_ch_slice)
        out_ch_slice = If(out_ch_slice >= CHout_Padding, CHout_Padding, out_ch_slice)
        CHout_Split_Times = If(out_ch_slice >= CHout_Padding, 1, (CHout_Padding+out_ch_slice-1)//out_ch_slice)
        out_ch_slice_last = If(CHout%out_ch_slice, CHout_Padding%out_ch_slice, out_ch_slice)

        CHout_Split_Times_minus1=CHout_Split_Times-1
        wt_size_in_bits = WT_SIZE_IN_BYTES // CHout_Padding * 8
        CHout = out_ch_slice
        CHout_last = out_ch_slice_last
        Last_Group_CHin = If(WT_CHin_Padding_with_Tin%WT_CH_Tgroup, WT_CHin_Padding_with_Tin%WT_CH_Tgroup, WT_CH_Tgroup)

        if Padding:
            feature_out_base = If(kvcache, DAT_OUT_BASE_ADDR + (Token-1)*Pixel_Data_Bytes, DAT_OUT_BASE_ADDR + last_token*Pixel_Data_Bytes)
            DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes*MAX_TOKEN
            DAT_OUT_SURFACE_STRIDE = Pixel_Data_Bytes*MAX_TOKEN*Hout
        else:
            DAT_IN_BASE_ADDR = If(kvcache_offset, DAT_IN_BASE_ADDR + (Token-1)*Pixel_Data_Bytes, DAT_IN_BASE_ADDR)
            DAT_IN_LINE_STRIDE = If(kvcache_offset, Pixel_Data_Bytes * Win, DAT_IN_LINE_STRIDE)
            DAT_IN_SURFACE_STRIDE = If(kvcache_offset, Pixel_Data_Bytes * Win * Hin, DAT_IN_SURFACE_STRIDE)
            feature_out_base = DAT_OUT_BASE_ADDR

        onchip = 0
        if DAT_IN_ONCHIP is not None:
            onchip += 0b1
            DAT_IN_BASE_ADDR = If(kvcache, DAT_IN_ONCHIP, DAT_IN_BASE_ADDR)
        if RES_IN_ONCHIP is not None:
            Res_Add_BASE_ADDR = If(kvcache, RES_IN_ONCHIP, Res_Add_BASE_ADDR)
        if DAT_OUT_ONCHIP is not None:
            onchip += 0b10
            feature_out_base = If(kvcache, DAT_OUT_ONCHIP, feature_out_base)
        if onchip:
            onchip = If(kvcache, onchip, 0)
        regs = []
        CSB_Write(regs, 2,CHin)
        CSB_Write(regs, 3,Win)
        CSB_Write(regs, 4,Hin)
        CSB_Write(regs, 5,Wout)
        CSB_Write(regs, 6,Hout)
        CSB_Write(regs, 7,CHout)
        CSB_Write(regs, 8,CHout_last)
        CSB_Write(regs, 9,If(kvcache, 1, (Token - last_token)))
        
        CSB_Write(regs, 10,DAT_IN_BASE_ADDR)
        CSB_Write(regs, 11,HBM00_WT_BASE_ADDR)
        CSB_Write(regs, 12,wt_size_in_bits)
        CSB_Write(regs, 13,feature_out_base)
        CSB_Write(regs, 14,CHout_Split_Times_minus1)
        CSB_Write(regs, 15,log2_WT_base_addr_Bank_Step)
        CSB_Write(regs, 16,(EW_MODE << 1) + RELU_EN)
        if AUGMAX_OUT_ADDR is not None:
            CSB_Write(regs, 17,AUGMAX_OUT_ADDR)
        else:
            CSB_Write(regs, 17,Skip_Factor-1)
        CSB_Write(regs, 18,onchip)
        
        CSB_Write(regs, 19,0)
        CSB_Write(regs, 20,0)
        CSB_Write(regs, 21,0)
        CSB_Write(regs, 22,reg_22)
        CSB_Write(regs, 23,reg_23)
        CSB_Write(regs, 24,Last_Group_CHin)
        CSB_Write(regs, 25,0)
        CSB_Write(regs, 26,BN_BASE_ADDR)
        CSB_Write(regs, 27,Res_Add_BASE_ADDR)
        CSB_Write(regs, 28,0)
        CSB_Write(regs, 29,DAT_IN_SURFACE_STRIDE)
        CSB_Write(regs, 30,DAT_IN_LINE_STRIDE)
        CSB_Write(regs, 31,DAT_OUT_SURFACE_STRIDE)
        CSB_Write(regs, 32,DAT_OUT_LINE_STRIDE)
        CSB_Write(regs, 33,mode)
        
        if AUGMAX_OUT_ADDR is not None:
            CSB_Read(regs, 40, 1)
        else:
            CSB_Read(regs, 1, 1)
        return regs


@Tasks.Register("accel.hbm.mvm", device.EdgeLLMv3)
def MVMBasic_v2(**kwargs):
    device = kwargs["device"]
    Token = kwargs["Token"]
    kvcache = kwargs.get("kvcache", 0)
    kvcache_offset = kwargs.get("kvcache_offset", 0)
    Head = kwargs.get("Head", 1)
    Padding = kwargs.get("padding", 0)
    Width_in = kwargs["Width_in"]
    Width_out = kwargs["Width_out"]
    EW_MODE = kwargs.get("EW_MODE", 0)
    RELU_EN = kwargs.get("RELU_EN", 0)
    DAT_OUT_LINE_STRIDE = kwargs.get("DAT_OUT_LINE_STRIDE")
    DAT_OUT_SURFACE_STRIDE = kwargs.get("DAT_OUT_SURFACE_STRIDE")
    DAT_IN_BASE_ADDR = kwargs.get("DAT_IN_BASE_ADDR")
    HBM00_WT_BASE_ADDR = kwargs.get("HBM00_WT_BASE_ADDR")
    BN_BASE_ADDR = kwargs.get("BN_BASE_ADDR")
    Res_Add_BASE_ADDR = kwargs.get("Res_Add_BASE_ADDR")
    DAT_OUT_BASE_ADDR = kwargs.get("DAT_OUT_BASE_ADDR")
    AUGMAX_OUT_ADDR = kwargs.get("AUGMAX_OUT_ADDR")
    log2_WT_base_addr_Bank_Step = kwargs.get("log2_WT_base_addr_Bank_Step", 28)
    Skip_Factor = kwargs.get("Skip_Factor", 1)
    last_token = kwargs.get("last_token", 0)
    full = kwargs.get("full", 0)
    DAT_IN_ONCHIP = kwargs.get("DAT_IN_ONCHIP")
    RES_IN_ONCHIP = kwargs.get("RES_IN_ONCHIP")
    DAT_OUT_ONCHIP = kwargs.get("DAT_OUT_ONCHIP")
    reg_22 = (device.log2_WT_CH_Tgroup << device.log2_CH) + device.WT_CH_Tgroup
    reg_23 = (device.log2_T_quant_block << device.log2_CH) + device.T_quant_block

    if full:
        Tin = device.base_Tin
        Tout = device.Tout
        Pixel_Data_Bytes = device.Pixel_Data_Bytes
        WT_DW = device.MAX_WT_DW
        HBM_AXI_DATA_WIDTH = device.HBM_AXI_DATA_WIDTH
        WT_CH_Tgroup = device.WT_CH_Tgroup
        MAX_WT_DW = device.MAX_WT_DW
        MAX_BN_DW = device.MAX_BN_DW
        HBM_Port = device.HBM_Port
        WT_BRAM_DEPTH = device.WT_BRAM_DEPTH
        AXI_BN_WIDTH = device.AXI_BN_WIDTH
        BN_FIFO_DEP = device.BN_FIFO_DEP
        BN_FIFO_NUM = device.BN_FIFO_NUM
        MAX_TOKEN = device.MAX_TOKEN

        real_Token = Token - last_token
        split_token = device.DAT_BRAM_DEPTH * device.Pixel_Data_Bytes // Width_in // 2
        for_k = Var("for_k")
        Win = If(kvcache, 1, If((real_Token) > split_token, If(for_k < Ceil(real_Token, split_token)-1, split_token, real_Token%split_token), real_Token))
        Hin = Head
        CHin = Width_in
        CHout = Width_out
        Wout = Win
        Hout = Hin
        CHout_div_Tout = ((CHout + Tout - 1) // Tout)
        CHin_div_Tout = ((CHin + Tout - 1) // Tout)
        CHin_Padding_with_Tout = CHin_div_Tout * Tout
        Tin_div_Tout = (Tin + Tout - 1) // Tout
        CHout_Padding = CHout_div_Tout * Tout
        DAT_IN_LINE_STRIDE = Pixel_Data_Bytes * If(kvcache, 1, real_Token)
        DAT_IN_SURFACE_STRIDE = Pixel_Data_Bytes * If(kvcache, 1, real_Token) * Hin
        if DAT_OUT_LINE_STRIDE is None or DAT_OUT_SURFACE_STRIDE is None:
            DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes * If(kvcache, 1, real_Token)
            DAT_OUT_SURFACE_STRIDE = Pixel_Data_Bytes * If(kvcache, 1, real_Token) * Hout

        WT_CHin_div_Tin = (CHin + Tin - 1) // Tin
        WT_CHin_Padding_with_Tin = WT_CHin_div_Tin*Tin
        WT_scale_group_nums = (WT_CHin_Padding_with_Tin + WT_CH_Tgroup-1) // WT_CH_Tgroup
        WT_scale_bits = CHout_Padding*HBM_AXI_DATA_WIDTH*WT_scale_group_nums
        WT_SIZE_IN_BYTES = (((CHout_Padding*WT_CHin_Padding_with_Tin*WT_DW)>>3)+((WT_scale_bits)>>3))

        if AUGMAX_OUT_ADDR is not None:
            if Res_Add_BASE_ADDR is not None:
                mode = MVMBNRESARG_MODE
            elif BN_BASE_ADDR is not None:
                mode = MVMBNARG_MODE
                Res_Add_BASE_ADDR = 0
            else:
                raise RuntimeError("MVM does NOT support ArgMax")
        elif Res_Add_BASE_ADDR is not None:
            mode = MVMBNRES_MODE
        elif BN_BASE_ADDR is not None:
            mode = MVMBN_MODE
            Res_Add_BASE_ADDR = 0
        else:
            mode = MVM_MODE
            BN_BASE_ADDR = 0
            Res_Add_BASE_ADDR = 0
        
        ## Hardware Testbench
        CHin = CHin_Padding_with_Tout
        CHout = CHout_Padding
        dat_num_per_row=Win*WT_CHin_div_Tin
        min_dat_depth=dat_num_per_row
        min_wt_depth=WT_CHin_div_Tin*((Tin*MAX_WT_DW)//HBM_AXI_DATA_WIDTH)*(Tout//HBM_Port)

        out_ch_slice=((WT_BRAM_DEPTH*2)//min_wt_depth)*Tout
        BN_FIFO_bits=AXI_BN_WIDTH*BN_FIFO_DEP*BN_FIFO_NUM
        BN_FIFO_chout_num=BN_FIFO_bits//(MAX_BN_DW*2)

        '''
        if out_ch_slice>BN_FIFO_chout_num and mode != MVM_MODE:
            out_ch_slice=BN_FIFO_chout_num

        if out_ch_slice>=CHout_Padding:
            out_ch_slice=CHout_Padding
            CHout_Split_Times=1
        else:
            CHout_Split_Times=(CHout_Padding+out_ch_slice-1)//out_ch_slice

        if CHout%out_ch_slice==0:
            out_ch_slice_last=out_ch_slice
        else:
            out_ch_slice_last=CHout_Padding%out_ch_slice
        '''
        if mode != MVM_MODE:
            out_ch_slice = If(out_ch_slice>BN_FIFO_chout_num, BN_FIFO_chout_num, out_ch_slice)
        out_ch_slice = If(out_ch_slice >= CHout_Padding, CHout_Padding, out_ch_slice)
        CHout_Split_Times = If(out_ch_slice >= CHout_Padding, 1, (CHout_Padding+out_ch_slice-1)//out_ch_slice)
        out_ch_slice_last = If(CHout%out_ch_slice, CHout_Padding%out_ch_slice, out_ch_slice)

        CHout_Split_Times_minus1=CHout_Split_Times-1
        wt_size_in_bits = WT_SIZE_IN_BYTES // CHout_Padding * 8
        CHout = out_ch_slice
        CHout_last = out_ch_slice_last
        Last_Group_CHin = If(WT_CHin_Padding_with_Tin%WT_CH_Tgroup, WT_CHin_Padding_with_Tin%WT_CH_Tgroup, WT_CH_Tgroup)

        if Padding:
            feature_out_base = If(kvcache, DAT_OUT_BASE_ADDR + (Token-1)*Pixel_Data_Bytes, DAT_OUT_BASE_ADDR + last_token*Pixel_Data_Bytes)
            DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes*MAX_TOKEN
            DAT_OUT_SURFACE_STRIDE = Pixel_Data_Bytes*MAX_TOKEN*Hout
        else:
            DAT_IN_BASE_ADDR = If(kvcache_offset, DAT_IN_BASE_ADDR + (real_Token-1)*Pixel_Data_Bytes, DAT_IN_BASE_ADDR)
            DAT_IN_LINE_STRIDE = If(kvcache_offset, Pixel_Data_Bytes * Win, DAT_IN_LINE_STRIDE)
            DAT_IN_SURFACE_STRIDE = If(kvcache_offset, Pixel_Data_Bytes * Win * Hin, DAT_IN_SURFACE_STRIDE)
            feature_out_base = DAT_OUT_BASE_ADDR

        regs = []
        @expr_for_hook(CSB_For, CSB_End)
        def for_mvm_single(k):
            DAT_IN_ADDR = DAT_IN_BASE_ADDR + k * split_token * Pixel_Data_Bytes
            RES_IN_ADDR = Res_Add_BASE_ADDR + If(mode==MVMBNRES_MODE or mode==MVMBNRESARG_MODE, k * split_token * Pixel_Data_Bytes, 0)
            DAT_OUT_ADDR = feature_out_base + k * split_token * Pixel_Data_Bytes
            onchip = 0
            if DAT_IN_ONCHIP is not None:
                onchip += 0b1
                DAT_IN_ADDR = If(kvcache, DAT_IN_ONCHIP, DAT_IN_ADDR)
            if RES_IN_ONCHIP is not None:
                RES_IN_ADDR = If(kvcache, RES_IN_ONCHIP, RES_IN_ADDR)
            if DAT_OUT_ONCHIP is not None:
                onchip += 0b10
                DAT_OUT_ADDR = If(kvcache, DAT_OUT_ONCHIP, DAT_OUT_ADDR)
            if onchip:
                onchip = If(kvcache, onchip, 0)
            CSB_Write(regs, 2,CHin)
            CSB_Write(regs, 3,Win)
            CSB_Write(regs, 4,Hin)
            CSB_Write(regs, 5,Wout)
            CSB_Write(regs, 6,Hout)
            CSB_Write(regs, 7,CHout)
            CSB_Write(regs, 8,CHout_last)
            CSB_Write(regs, 9,Win)
            
            CSB_Write(regs, 10,DAT_IN_ADDR)
            CSB_Write(regs, 11,HBM00_WT_BASE_ADDR)
            CSB_Write(regs, 12,wt_size_in_bits)
            CSB_Write(regs, 13,DAT_OUT_ADDR)
            CSB_Write(regs, 14,CHout_Split_Times_minus1)
            CSB_Write(regs, 15,log2_WT_base_addr_Bank_Step)
            CSB_Write(regs, 16,(EW_MODE << 1) + RELU_EN)
            if AUGMAX_OUT_ADDR is not None:
                CSB_Write(regs, 17,AUGMAX_OUT_ADDR)
            else:
                CSB_Write(regs, 17,Skip_Factor-1)
            CSB_Write(regs, 18,onchip)
            
            CSB_Write(regs, 19,0)
            CSB_Write(regs, 20,0)
            CSB_Write(regs, 21,0)
            CSB_Write(regs, 22,reg_22)
            CSB_Write(regs, 23,reg_23)
            CSB_Write(regs, 24,Last_Group_CHin)
            CSB_Write(regs, 25,0)
            CSB_Write(regs, 26,BN_BASE_ADDR)
            CSB_Write(regs, 27,RES_IN_ADDR)
            CSB_Write(regs, 28,0)
            CSB_Write(regs, 29,DAT_IN_SURFACE_STRIDE)
            CSB_Write(regs, 30,DAT_IN_LINE_STRIDE)
            CSB_Write(regs, 31,DAT_OUT_SURFACE_STRIDE)
            CSB_Write(regs, 32,DAT_OUT_LINE_STRIDE)
            CSB_Write(regs, 33,mode)
            
            if AUGMAX_OUT_ADDR is not None:
                CSB_Read(regs, 40, 1)
            else:
                CSB_Read(regs, 1, 1)
        for_expr_k = For(for_k, Numb(0), If(kvcache, 1, Ceil(real_Token, split_token)), for_mvm_single)
        for_expr_k.run(regs)
        return regs
    else:
        Tin = device.base_Tin
        Tout = device.Tout
        Pixel_Data_Bytes = device.Pixel_Data_Bytes
        WT_DW = device.MAX_WT_DW
        HBM_AXI_DATA_WIDTH = device.HBM_AXI_DATA_WIDTH
        WT_CH_Tgroup = device.WT_CH_Tgroup
        MAX_WT_DW = device.MAX_WT_DW
        MAX_BN_DW = device.MAX_BN_DW
        HBM_Port = device.HBM_Port
        WT_BRAM_DEPTH = device.WT_BRAM_DEPTH
        AXI_BN_WIDTH = device.AXI_BN_WIDTH
        BN_FIFO_DEP = device.BN_FIFO_DEP
        BN_FIFO_NUM = device.BN_FIFO_NUM
        MAX_TOKEN = device.MAX_TOKEN

        Win = If(kvcache, 1, Token - last_token)
        Hin = Head
        CHin = Width_in
        CHout = Width_out
        Wout = Win
        Hout = Hin
        CHout_div_Tout = ((CHout + Tout - 1) // Tout)
        CHin_div_Tout = ((CHin + Tout - 1) // Tout)
        CHin_Padding_with_Tout = CHin_div_Tout * Tout
        Tin_div_Tout = (Tin + Tout - 1) // Tout
        CHout_Padding = CHout_div_Tout * Tout
        DAT_IN_LINE_STRIDE = Pixel_Data_Bytes * Win
        DAT_IN_SURFACE_STRIDE = Pixel_Data_Bytes * Win * Hin
        if DAT_OUT_LINE_STRIDE is None or DAT_OUT_SURFACE_STRIDE is None:
            DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes * Wout
            DAT_OUT_SURFACE_STRIDE = Pixel_Data_Bytes * Wout * Hout

        WT_CHin_div_Tin = (CHin + Tin - 1) // Tin
        WT_CHin_Padding_with_Tin = WT_CHin_div_Tin*Tin
        WT_scale_group_nums = (WT_CHin_Padding_with_Tin + WT_CH_Tgroup-1) // WT_CH_Tgroup
        WT_scale_bits = CHout_Padding*HBM_AXI_DATA_WIDTH*WT_scale_group_nums
        WT_SIZE_IN_BYTES = (((CHout_Padding*WT_CHin_Padding_with_Tin*WT_DW)>>3)+((WT_scale_bits)>>3))

        if AUGMAX_OUT_ADDR is not None:
            if Res_Add_BASE_ADDR is not None:
                mode = MVMBNRESARG_MODE
            elif BN_BASE_ADDR is not None:
                mode = MVMBNARG_MODE
                Res_Add_BASE_ADDR = 0
            else:
                raise RuntimeError("MVM does NOT support ArgMax")
        elif Res_Add_BASE_ADDR is not None:
            mode = MVMBNRES_MODE
        elif BN_BASE_ADDR is not None:
            mode = MVMBN_MODE
            Res_Add_BASE_ADDR = 0
        else:
            mode = MVM_MODE
            BN_BASE_ADDR = 0
            Res_Add_BASE_ADDR = 0
        
        ## Hardware Testbench
        CHin = CHin_Padding_with_Tout
        CHout = CHout_Padding
        dat_num_per_row=Win*WT_CHin_div_Tin
        min_dat_depth=dat_num_per_row
        min_wt_depth=WT_CHin_div_Tin*((Tin*MAX_WT_DW)//HBM_AXI_DATA_WIDTH)*(Tout//HBM_Port)

        out_ch_slice=((WT_BRAM_DEPTH*2)//min_wt_depth)*Tout
        BN_FIFO_bits=AXI_BN_WIDTH*BN_FIFO_DEP*BN_FIFO_NUM
        BN_FIFO_chout_num=BN_FIFO_bits//(MAX_BN_DW*2)

        '''
        if out_ch_slice>BN_FIFO_chout_num and mode != MVM_MODE:
            out_ch_slice=BN_FIFO_chout_num

        if out_ch_slice>=CHout_Padding:
            out_ch_slice=CHout_Padding
            CHout_Split_Times=1
        else:
            CHout_Split_Times=(CHout_Padding+out_ch_slice-1)//out_ch_slice

        if CHout%out_ch_slice==0:
            out_ch_slice_last=out_ch_slice
        else:
            out_ch_slice_last=CHout_Padding%out_ch_slice
        '''
        if mode != MVM_MODE:
            out_ch_slice = If(out_ch_slice>BN_FIFO_chout_num, BN_FIFO_chout_num, out_ch_slice)
        out_ch_slice = If(out_ch_slice >= CHout_Padding, CHout_Padding, out_ch_slice)
        CHout_Split_Times = If(out_ch_slice >= CHout_Padding, 1, (CHout_Padding+out_ch_slice-1)//out_ch_slice)
        out_ch_slice_last = If(CHout%out_ch_slice, CHout_Padding%out_ch_slice, out_ch_slice)

        CHout_Split_Times_minus1=CHout_Split_Times-1
        wt_size_in_bits = WT_SIZE_IN_BYTES // CHout_Padding * 8
        CHout = out_ch_slice
        CHout_last = out_ch_slice_last
        Last_Group_CHin = If(WT_CHin_Padding_with_Tin%WT_CH_Tgroup, WT_CHin_Padding_with_Tin%WT_CH_Tgroup, WT_CH_Tgroup)

        if Padding:
            feature_out_base = If(kvcache, DAT_OUT_BASE_ADDR + (Token-1)*Pixel_Data_Bytes, DAT_OUT_BASE_ADDR + last_token*Pixel_Data_Bytes)
            DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes*MAX_TOKEN
            DAT_OUT_SURFACE_STRIDE = Pixel_Data_Bytes*MAX_TOKEN*Hout
        else:
            DAT_IN_BASE_ADDR = If(kvcache_offset, DAT_IN_BASE_ADDR + (Token-1)*Pixel_Data_Bytes, DAT_IN_BASE_ADDR)
            DAT_IN_LINE_STRIDE = If(kvcache_offset, Pixel_Data_Bytes * Win, DAT_IN_LINE_STRIDE)
            DAT_IN_SURFACE_STRIDE = If(kvcache_offset, Pixel_Data_Bytes * Win * Hin, DAT_IN_SURFACE_STRIDE)
            feature_out_base = DAT_OUT_BASE_ADDR

        onchip = 0
        if DAT_IN_ONCHIP is not None:
            onchip += 0b1
            DAT_IN_BASE_ADDR = If(kvcache, DAT_IN_ONCHIP, DAT_IN_BASE_ADDR)
        if RES_IN_ONCHIP is not None:
            Res_Add_BASE_ADDR = If(kvcache, RES_IN_ONCHIP, Res_Add_BASE_ADDR)
        if DAT_OUT_ONCHIP is not None:
            onchip += 0b10
            feature_out_base = If(kvcache, DAT_OUT_ONCHIP, feature_out_base)
        if onchip:
            onchip = If(kvcache, onchip, 0)
        regs = []
        CSB_Write(regs, 2,CHin)
        CSB_Write(regs, 3,Win)
        CSB_Write(regs, 4,Hin)
        CSB_Write(regs, 5,Wout)
        CSB_Write(regs, 6,Hout)
        CSB_Write(regs, 7,CHout)
        CSB_Write(regs, 8,CHout_last)
        CSB_Write(regs, 9,If(kvcache, 1, (Token - last_token)))
        
        CSB_Write(regs, 10,DAT_IN_BASE_ADDR)
        CSB_Write(regs, 11,HBM00_WT_BASE_ADDR)
        CSB_Write(regs, 12,wt_size_in_bits)
        CSB_Write(regs, 13,feature_out_base)
        CSB_Write(regs, 14,CHout_Split_Times_minus1)
        CSB_Write(regs, 15,log2_WT_base_addr_Bank_Step)
        CSB_Write(regs, 16,(EW_MODE << 1) + RELU_EN)
        if AUGMAX_OUT_ADDR is not None:
            CSB_Write(regs, 17,AUGMAX_OUT_ADDR)
        else:
            CSB_Write(regs, 17,Skip_Factor-1)
        CSB_Write(regs, 18,onchip)
        
        CSB_Write(regs, 19,0)
        CSB_Write(regs, 20,0)
        CSB_Write(regs, 21,0)
        CSB_Write(regs, 22,reg_22)
        CSB_Write(regs, 23,reg_23)
        CSB_Write(regs, 24,Last_Group_CHin)
        CSB_Write(regs, 25,0)
        CSB_Write(regs, 26,BN_BASE_ADDR)
        CSB_Write(regs, 27,Res_Add_BASE_ADDR)
        CSB_Write(regs, 28,0)
        CSB_Write(regs, 29,DAT_IN_SURFACE_STRIDE)
        CSB_Write(regs, 30,DAT_IN_LINE_STRIDE)
        CSB_Write(regs, 31,DAT_OUT_SURFACE_STRIDE)
        CSB_Write(regs, 32,DAT_OUT_LINE_STRIDE)
        CSB_Write(regs, 33,mode)
        
        CSB_Read(regs, 1, 1)
        return regs


@Tasks.Register("accel.hbm.layer_norm", device.HBM0603)
def LayerNorm(**kwargs):
    device = kwargs["device"]
    Token = kwargs["Token"]
    kvcache = kwargs.get("kvcache", 0)
    Head = kwargs.get("Head", 1)
    Width_in = kwargs["Width_in"]
    RMS_Norm = kwargs.get("RMS_Norm", 0)
    kvcache_offset = kwargs.get("kvcache_offset", 0)
    DAT_OUT_LINE_STRIDE = kwargs.get("DAT_OUT_LINE_STRIDE")
    DAT_OUT_SURFACE_STRIDE = kwargs.get("DAT_OUT_SURFACE_STRIDE")
    DAT_IN_BASE_ADDR = kwargs.get("DAT_IN_BASE_ADDR")
    LN_WT_BASE_ADDR = kwargs.get("LN_WT_BASE_ADDR")
    DAT_OUT_BASE_ADDR = kwargs.get("DAT_OUT_BASE_ADDR")
    last_token = kwargs.get("last_token", 0)

    Tin = device.base_Tin
    Tout = device.Tout
    Pixel_Data_Bytes = device.Pixel_Data_Bytes
    WT_DW = device.MAX_WT_DW
    HBM_AXI_DATA_WIDTH = device.HBM_AXI_DATA_WIDTH
    WT_CH_Tgroup = device.WT_CH_Tgroup
    MAX_WT_DW = device.MAX_WT_DW
    MAX_BN_DW = device.MAX_BN_DW
    HBM_Port = device.HBM_Port
    WT_BRAM_DEPTH = device.WT_BRAM_DEPTH
    AXI_BN_WIDTH = device.AXI_BN_WIDTH
    BN_FIFO_DEP = device.BN_FIFO_DEP
    BN_FIFO_NUM = device.BN_FIFO_NUM
    AXI_DAT_WIDTH = device.AXI_DAT_WIDTH
    log2_AXI_BURST_LEN = device.log2_AXI_BURST_LEN

    Win = If(kvcache, 1, Token - last_token)
    Hin = Head
    CHin = Width_in
    CHout = CHin
    Wout = Win
    Hout = Hin
    Layer_Norm = 1 - RMS_Norm
    CHout_div_Tout = ((CHout + Tout - 1) // Tout)
    CHin_div_Tout = ((CHin + Tout - 1) // Tout)
    CHin_Padding_with_Tout = CHin_div_Tout * Tout
    LN_num_per_AXI_DW = AXI_DAT_WIDTH // (2*MAX_BN_DW)
    Tin_div_Tout = (Tin + Tout - 1) // Tout
    CHout_Padding = CHout_div_Tout * Tout
    DAT_IN_LINE_STRIDE = Pixel_Data_Bytes * Win
    DAT_IN_SURFACE_STRIDE = Pixel_Data_Bytes * Win * Hin
    if DAT_OUT_LINE_STRIDE is None or DAT_OUT_SURFACE_STRIDE is None:
        DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes * Wout
        DAT_OUT_SURFACE_STRIDE = Pixel_Data_Bytes * Wout * Hout
    DAT_IN_BASE_ADDR = If(kvcache_offset, DAT_IN_BASE_ADDR + ((Token - last_token)-1)*Pixel_Data_Bytes, DAT_IN_BASE_ADDR)
    DAT_IN_LINE_STRIDE = If(kvcache_offset, Pixel_Data_Bytes * (Token - last_token), DAT_IN_LINE_STRIDE)
    DAT_IN_SURFACE_STRIDE = If(kvcache_offset, Pixel_Data_Bytes * (Token - last_token) * Hin, DAT_IN_SURFACE_STRIDE)

    ## Hardware Testbench
    CHin = CHin_Padding_with_Tout
    recip_ch = 1 / CHin
    FP20_recip_CH_r = FP32_to_FP20(recip_ch)
    LN_CH_burst_times_minus1=(CHin//LN_num_per_AXI_DW)>>log2_AXI_BURST_LEN
    pixel_in = Win
    ch_out = Width_in
    Ln_reg_bias = 192
    
    regs = []
    CSB_Write(regs, Ln_reg_bias+2 , LN_WT_BASE_ADDR         )
    CSB_Write(regs, Ln_reg_bias+3 , DAT_IN_BASE_ADDR        )
    CSB_Write(regs, Ln_reg_bias+4 , DAT_IN_SURFACE_STRIDE   )
    CSB_Write(regs, Ln_reg_bias+5 , DAT_IN_LINE_STRIDE      )
    CSB_Write(regs, Ln_reg_bias+6 , DAT_OUT_BASE_ADDR       )
    CSB_Write(regs, Ln_reg_bias+7 , DAT_OUT_SURFACE_STRIDE  )
    CSB_Write(regs, Ln_reg_bias+8 , DAT_OUT_LINE_STRIDE     )
    CSB_Write(regs, Ln_reg_bias+9 , (CHin+Tout-1)//Tout     )
    CSB_Write(regs, Ln_reg_bias+10, Hin                     )
    CSB_Write(regs, Ln_reg_bias+11, Win                     )
    CSB_Write(regs, Ln_reg_bias+12, pixel_in                )
    CSB_Write(regs, Ln_reg_bias+13, FP20_recip_CH_r         )
    CSB_Write(regs, Ln_reg_bias+14, LN_CH_burst_times_minus1)
    CSB_Write(regs, Ln_reg_bias+15,               Layer_Norm)
    CSB_Write(regs, Ln_reg_bias+16,                        0)
    CSB_Write(regs, Ln_reg_bias+17,                0b10_0000)
    CSB_Read(regs, Ln_reg_bias+1, 1)
    return regs


@Tasks.Register("accel.hbm.layer_norm", device.EdgeLLMv2)
def LayerNorm_v2(**kwargs):
    device = kwargs["device"]
    Token = kwargs["Token"]
    kvcache = kwargs.get("kvcache", 0)
    Head = kwargs.get("Head", 1)
    Width_in = kwargs["Width_in"]
    RMS_Norm = kwargs.get("RMS_Norm", 0)
    kvcache_offset = kwargs.get("kvcache_offset", 0)
    DAT_OUT_LINE_STRIDE = kwargs.get("DAT_OUT_LINE_STRIDE")
    DAT_OUT_SURFACE_STRIDE = kwargs.get("DAT_OUT_SURFACE_STRIDE")
    DAT_IN_BASE_ADDR = kwargs.get("DAT_IN_BASE_ADDR")
    LN_WT_BASE_ADDR = kwargs.get("LN_WT_BASE_ADDR")
    DAT_OUT_BASE_ADDR = kwargs.get("DAT_OUT_BASE_ADDR")
    last_token = kwargs.get("last_token", 0)
    DAT_IN_ONCHIP = kwargs.get("DAT_IN_ONCHIP")
    DAT_OUT_ONCHIP = kwargs.get("DAT_OUT_ONCHIP")

    Tin = device.base_Tin
    Tout = device.Tout
    Pixel_Data_Bytes = device.Pixel_Data_Bytes
    WT_DW = device.MAX_WT_DW
    HBM_AXI_DATA_WIDTH = device.HBM_AXI_DATA_WIDTH
    WT_CH_Tgroup = device.WT_CH_Tgroup
    MAX_WT_DW = device.MAX_WT_DW
    MAX_BN_DW = device.MAX_BN_DW
    HBM_Port = device.HBM_Port
    WT_BRAM_DEPTH = device.WT_BRAM_DEPTH
    AXI_BN_WIDTH = device.AXI_BN_WIDTH
    BN_FIFO_DEP = device.BN_FIFO_DEP
    BN_FIFO_NUM = device.BN_FIFO_NUM
    AXI_DAT_WIDTH = device.AXI_DAT_WIDTH
    log2_AXI_BURST_LEN = device.log2_AXI_BURST_LEN

    Win = If(kvcache, 1, Token - last_token)
    Hin = Head
    CHin = Width_in
    CHout = CHin
    Wout = Win
    Hout = Hin
    Layer_Norm = 1 - RMS_Norm
    CHout_div_Tout = ((CHout + Tout - 1) // Tout)
    CHin_div_Tout = ((CHin + Tout - 1) // Tout)
    CHin_Padding_with_Tout = CHin_div_Tout * Tout
    LN_num_per_AXI_DW = AXI_DAT_WIDTH // (2*MAX_BN_DW)
    Tin_div_Tout = (Tin + Tout - 1) // Tout
    CHout_Padding = CHout_div_Tout * Tout
    DAT_IN_LINE_STRIDE = Pixel_Data_Bytes * Win
    DAT_IN_SURFACE_STRIDE = Pixel_Data_Bytes * Win * Hin
    if DAT_OUT_LINE_STRIDE is None or DAT_OUT_SURFACE_STRIDE is None:
        DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes * Wout
        DAT_OUT_SURFACE_STRIDE = Pixel_Data_Bytes * Wout * Hout
    DAT_IN_BASE_ADDR = If(kvcache_offset, DAT_IN_BASE_ADDR + ((Token - last_token)-1)*Pixel_Data_Bytes, DAT_IN_BASE_ADDR)
    DAT_IN_LINE_STRIDE = If(kvcache_offset, Pixel_Data_Bytes * (Token - last_token), DAT_IN_LINE_STRIDE)
    DAT_IN_SURFACE_STRIDE = If(kvcache_offset, Pixel_Data_Bytes * (Token - last_token) * Hin, DAT_IN_SURFACE_STRIDE)

    ## Hardware Testbench
    CHin = CHin_Padding_with_Tout
    recip_ch = 1 / CHin
    FP20_recip_CH_r = FP32_to_FP20(recip_ch)
    LN_CH_burst_times_minus1=(CHin//LN_num_per_AXI_DW)>>log2_AXI_BURST_LEN
    pixel_in = Win
    ch_out = Width_in
    Ln_reg_bias = 192
    
    onchip = 0
    if DAT_IN_ONCHIP is not None:
        onchip += 0b1
        DAT_IN_BASE_ADDR = If(kvcache, DAT_IN_ONCHIP, DAT_IN_BASE_ADDR)
    if DAT_OUT_ONCHIP is not None:
        onchip += 0b10
        DAT_OUT_BASE_ADDR = If(kvcache, DAT_OUT_ONCHIP, DAT_OUT_BASE_ADDR)

    if onchip:
        onchip = If(kvcache, onchip, 0)
    regs = []
    CSB_Write(regs, Ln_reg_bias+2 , LN_WT_BASE_ADDR         )
    CSB_Write(regs, Ln_reg_bias+3 , DAT_IN_BASE_ADDR        )
    CSB_Write(regs, Ln_reg_bias+4 , DAT_IN_SURFACE_STRIDE   )
    CSB_Write(regs, Ln_reg_bias+5 , DAT_IN_LINE_STRIDE      )
    CSB_Write(regs, Ln_reg_bias+6 , DAT_OUT_BASE_ADDR       )
    CSB_Write(regs, Ln_reg_bias+7 , DAT_OUT_SURFACE_STRIDE  )
    CSB_Write(regs, Ln_reg_bias+8 , DAT_OUT_LINE_STRIDE     )
    CSB_Write(regs, Ln_reg_bias+9 , (CHin+Tout-1)//Tout     )
    CSB_Write(regs, Ln_reg_bias+10, Hin                     )
    CSB_Write(regs, Ln_reg_bias+11, Win                     )
    CSB_Write(regs, Ln_reg_bias+12, pixel_in                )
    CSB_Write(regs, Ln_reg_bias+13, FP20_recip_CH_r         )
    CSB_Write(regs, Ln_reg_bias+14, LN_CH_burst_times_minus1)
    CSB_Write(regs, Ln_reg_bias+15,               Layer_Norm)
    CSB_Write(regs, Ln_reg_bias+16,                   onchip)
    CSB_Write(regs, Ln_reg_bias+17,                0b10_0000)
    CSB_Read(regs, Ln_reg_bias+1, 1)
    return regs


@Tasks.Register("accel.hbm.eleminatewise", device.HBM0603)
def EleminateWise(**kwargs):
    device = kwargs["device"]
    Token = kwargs["Token"]
    kvcache = kwargs.get("kvcache", 0)
    Mode = kwargs["Mode"]
    Width_in = kwargs["Width_in"]
    A_DAT_IN_BASE_ADDR = None
    B_DAT_IN_BASE_ADDR = None
    DAT_OUT_BASE_ADDR = kwargs.get("DAT_OUT_BASE_ADDR")
    last_token = kwargs.get("last_token", 0)

    Tin = device.base_Tin
    Tout = device.Tout
    Pixel_Data_Bytes = device.Pixel_Data_Bytes
    WT_DW = device.MAX_WT_DW
    HBM_AXI_DATA_WIDTH = device.HBM_AXI_DATA_WIDTH
    WT_CH_Tgroup = device.WT_CH_Tgroup
    MAX_WT_DW = device.MAX_WT_DW
    MAX_BN_DW = device.MAX_BN_DW
    HBM_Port = device.HBM_Port
    WT_BRAM_DEPTH = device.WT_BRAM_DEPTH
    AXI_BN_WIDTH = device.AXI_BN_WIDTH
    BN_FIFO_DEP = device.BN_FIFO_DEP
    BN_FIFO_NUM = device.BN_FIFO_NUM
    AXI_DAT_WIDTH = device.AXI_DAT_WIDTH
    log2_AXI_BURST_LEN = device.log2_AXI_BURST_LEN

    Win = If(kvcache, 1, Token - last_token)
    Hin = 1
    CHin = Width_in
    CHout = CHin
    Wout = kwargs.get("Wout", Win)
    Hout = Hin
    CHout_div_Tout = ((CHout + Tout - 1) // Tout)
    CHin_div_Tout = ((CHin + Tout - 1) // Tout)
    CHin_Padding_with_Tout = CHin_div_Tout * Tout
    LN_num_per_AXI_DW = AXI_DAT_WIDTH // (2*MAX_BN_DW)
    Tin_div_Tout = (Tin + Tout - 1) // Tout
    CHout_Padding = CHout_div_Tout * Tout
    DAT_IN_LINE_STRIDE = Pixel_Data_Bytes * Win
    DAT_IN_SURFACE_STRIDE = Pixel_Data_Bytes * Win * Hin
    DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes * Wout
    DAT_OUT_SURFACE_STRIDE = Pixel_Data_Bytes * Wout * Hout

    ## Hardware Testbench
    Elementwise_reg_bias = 128
    
    regs = []
    CSB_Write(regs, Elementwise_reg_bias+2 , Mode         )
    CSB_Write(regs, Elementwise_reg_bias+3 , A_DAT_IN_BASE_ADDR      )
    CSB_Write(regs, Elementwise_reg_bias+4 , DAT_IN_SURFACE_STRIDE   )
    CSB_Write(regs, Elementwise_reg_bias+5 , DAT_IN_LINE_STRIDE      )
    CSB_Write(regs, Elementwise_reg_bias+6 , DAT_OUT_BASE_ADDR       )
    CSB_Write(regs, Elementwise_reg_bias+7 , DAT_OUT_SURFACE_STRIDE  )
    CSB_Write(regs, Elementwise_reg_bias+8 , DAT_OUT_LINE_STRIDE     )
    CSB_Write(regs, Elementwise_reg_bias+9 , (CHin+Tout-1)//Tout     )
    CSB_Write(regs, Elementwise_reg_bias+10, Hin                     )
    CSB_Write(regs, Elementwise_reg_bias+11, Win                     )
    CSB_Write(regs, Elementwise_reg_bias+12, B_DAT_IN_BASE_ADDR      )
    CSB_Write(regs, Elementwise_reg_bias+13,                        0)
    CSB_Write(regs, Elementwise_reg_bias+14,                0b00_0001)
    CSB_Read(regs, Elementwise_reg_bias+1, 1)
    return regs


@Tasks.Register("accel.hbm.eleminatewise", device.EdgeLLMv2)
def EleminateWise_v2(**kwargs):
    device = kwargs["device"]
    Token = kwargs["Token"]
    kvcache = kwargs.get("kvcache", 0)
    Mode = kwargs["Mode"]
    Width_in = kwargs["Width_in"]
    A_DAT_IN_BASE_ADDR = kwargs.get("A_DAT_IN_BASE_ADDR")
    B_DAT_IN_BASE_ADDR = kwargs.get("B_DAT_IN_BASE_ADDR")
    DAT_OUT_BASE_ADDR = kwargs.get("DAT_OUT_BASE_ADDR")
    last_token = kwargs.get("last_token", 0)
    A_DAT_IN_ONCHIP = kwargs.get("A_DAT_IN_ONCHIP")
    B_DAT_IN_ONCHIP = kwargs.get("B_DAT_IN_ONCHIP")
    DAT_OUT_ONCHIP = kwargs.get("DAT_OUT_ONCHIP")

    Tin = device.base_Tin
    Tout = device.Tout
    Pixel_Data_Bytes = device.Pixel_Data_Bytes
    WT_DW = device.MAX_WT_DW
    HBM_AXI_DATA_WIDTH = device.HBM_AXI_DATA_WIDTH
    WT_CH_Tgroup = device.WT_CH_Tgroup
    MAX_WT_DW = device.MAX_WT_DW
    MAX_BN_DW = device.MAX_BN_DW
    HBM_Port = device.HBM_Port
    WT_BRAM_DEPTH = device.WT_BRAM_DEPTH
    AXI_BN_WIDTH = device.AXI_BN_WIDTH
    BN_FIFO_DEP = device.BN_FIFO_DEP
    BN_FIFO_NUM = device.BN_FIFO_NUM
    AXI_DAT_WIDTH = device.AXI_DAT_WIDTH
    log2_AXI_BURST_LEN = device.log2_AXI_BURST_LEN

    Win = If(kvcache, 1, Token - last_token)
    Hin = 1
    CHin = Width_in
    CHout = CHin
    Wout = kwargs.get("Wout", Win)
    Hout = Hin
    CHout_div_Tout = ((CHout + Tout - 1) // Tout)
    CHin_div_Tout = ((CHin + Tout - 1) // Tout)
    CHin_Padding_with_Tout = CHin_div_Tout * Tout
    LN_num_per_AXI_DW = AXI_DAT_WIDTH // (2*MAX_BN_DW)
    Tin_div_Tout = (Tin + Tout - 1) // Tout
    CHout_Padding = CHout_div_Tout * Tout
    DAT_IN_LINE_STRIDE = Pixel_Data_Bytes * Win
    DAT_IN_SURFACE_STRIDE = Pixel_Data_Bytes * Win * Hin
    DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes * Wout
    DAT_OUT_SURFACE_STRIDE = Pixel_Data_Bytes * Wout * Hout

    ## Hardware Testbench
    Elementwise_reg_bias = 128

    onchip = 0
    if A_DAT_IN_ONCHIP is not None:
        onchip += 0b1
        A_DAT_IN_BASE_ADDR = If(kvcache, A_DAT_IN_ONCHIP, A_DAT_IN_BASE_ADDR)
        B_DAT_IN_BASE_ADDR = If(kvcache, B_DAT_IN_ONCHIP, B_DAT_IN_BASE_ADDR)
    if DAT_OUT_ONCHIP is not None:
        onchip += 0b10
        DAT_OUT_BASE_ADDR = If(kvcache, DAT_OUT_ONCHIP, DAT_OUT_BASE_ADDR)

    if onchip:
        onchip = If(kvcache, onchip, 0)
    regs = []
    CSB_Write(regs, Elementwise_reg_bias+2 , Mode         )
    CSB_Write(regs, Elementwise_reg_bias+3 , A_DAT_IN_BASE_ADDR      )
    CSB_Write(regs, Elementwise_reg_bias+4 , DAT_IN_SURFACE_STRIDE   )
    CSB_Write(regs, Elementwise_reg_bias+5 , DAT_IN_LINE_STRIDE      )
    CSB_Write(regs, Elementwise_reg_bias+6 , DAT_OUT_BASE_ADDR       )
    CSB_Write(regs, Elementwise_reg_bias+7 , DAT_OUT_SURFACE_STRIDE  )
    CSB_Write(regs, Elementwise_reg_bias+8 , DAT_OUT_LINE_STRIDE     )
    CSB_Write(regs, Elementwise_reg_bias+9 , (CHin+Tout-1)//Tout     )
    CSB_Write(regs, Elementwise_reg_bias+10, Hin                     )
    CSB_Write(regs, Elementwise_reg_bias+11, Win                     )
    CSB_Write(regs, Elementwise_reg_bias+12, B_DAT_IN_BASE_ADDR      )
    CSB_Write(regs, Elementwise_reg_bias+13,                   onchip)
    CSB_Write(regs, Elementwise_reg_bias+14,                0b00_0001)
    CSB_Read(regs, Elementwise_reg_bias+1, 1)
    return regs


@Tasks.Register("accel.hbm.pos_emb", device.HBM0603)
def PosEmb(**kwargs):
    device = kwargs["device"]
    Token = kwargs["Token"]
    kvcache = kwargs.get("kvcache", 0)
    Head = kwargs.get("Head", 1)
    Feature_Head = kwargs["Feature_Head"]
    DAT_IN_BASE_ADDR = kwargs.get("DAT_IN_BASE_ADDR")
    POS_IN_BASE_ADDR = kwargs.get("POS_IN_BASE_ADDR")
    DAT_OUT_BASE_ADDR = kwargs.get("DAT_OUT_BASE_ADDR")
    last_token = kwargs.get("last_token", 0)
    padding = kwargs.get("padding", 0)
    DAT_IN_ONCHIP = None
    DAT_OUT_ONCHIP = None

    Tin = device.base_Tin
    Tout = device.Tout
    Pixel_Data_Bytes = device.Pixel_Data_Bytes
    WT_DW = device.MAX_WT_DW
    HBM_AXI_DATA_WIDTH = device.HBM_AXI_DATA_WIDTH
    WT_CH_Tgroup = device.WT_CH_Tgroup
    MAX_WT_DW = device.MAX_WT_DW
    MAX_BN_DW = device.MAX_BN_DW
    HBM_Port = device.HBM_Port
    WT_BRAM_DEPTH = device.WT_BRAM_DEPTH
    AXI_BN_WIDTH = device.AXI_BN_WIDTH
    BN_FIFO_DEP = device.BN_FIFO_DEP
    BN_FIFO_NUM = device.BN_FIFO_NUM
    AXI_DAT_WIDTH = device.AXI_DAT_WIDTH
    log2_AXI_BURST_LEN = device.log2_AXI_BURST_LEN
    MAX_TOKEN = device.MAX_TOKEN
    MAX_CH_per_HEAD = device.MAX_CH_per_HEAD

    Dynamic_Token = If(kvcache, 1, Token - last_token)
    Win = Dynamic_Token
    Hin = Head
    CHin = MAX_CH_per_HEAD
    CHout = CHin
    if padding:
        Wout = MAX_TOKEN
    else:
        Wout = Win
    Hout = Hin
    CHout_div_Tout = ((CHout + Tout - 1) // Tout)
    CHin_div_Tout = ((CHin + Tout - 1) // Tout)
    CHin_Padding_with_Tout = CHin_div_Tout * Tout
    LN_num_per_AXI_DW = AXI_DAT_WIDTH // (2*MAX_BN_DW)
    Tin_div_Tout = (Tin + Tout - 1) // Tout
    CHout_Padding = CHout_div_Tout * Tout
    DAT_IN_LINE_STRIDE = Pixel_Data_Bytes * Win
    DAT_IN_HEAD_STRIDE = Pixel_Data_Bytes * Win * Hin * CHin_div_Tout
    DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes * Wout
    DAT_OUT_HEAD_STRIDE = Pixel_Data_Bytes * Wout * Hout * CHout_div_Tout
    POS_HEAD_STRIDE = Pixel_Data_Bytes*MAX_TOKEN*CHin_div_Tout//2
    POS_LINE_STRIDE = Pixel_Data_Bytes*MAX_TOKEN

    ## Hardware Testbench
    PosEmb_reg_bias=192
    feature_in_base=DAT_IN_BASE_ADDR
    if padding:
        feature_out_base=If(kvcache, DAT_OUT_BASE_ADDR+(Token-1)*Pixel_Data_Bytes, DAT_OUT_BASE_ADDR+last_token*Pixel_Data_Bytes)
    else:
        feature_out_base = DAT_OUT_BASE_ADDR
    PosEmb_in_base=If(kvcache, POS_IN_BASE_ADDR+(Token-1)*Pixel_Data_Bytes, POS_IN_BASE_ADDR+last_token*Pixel_Data_Bytes)

    onchip = 0
    if DAT_IN_ONCHIP is not None:
        onchip += 0b1
        feature_in_base = If(kvcache, DAT_IN_ONCHIP, feature_in_base)
    if DAT_OUT_ONCHIP is not None:
        onchip += 0b10
        feature_out_base = If(kvcache, DAT_OUT_ONCHIP, feature_out_base)
    regs = [] 
    CSB_Write(regs, PosEmb_reg_bias+2 ,PosEmb_in_base         )
    CSB_Write(regs, PosEmb_reg_bias+3 ,feature_in_base        )
    CSB_Write(regs, PosEmb_reg_bias+4 ,DAT_IN_HEAD_STRIDE     )
    CSB_Write(regs, PosEmb_reg_bias+5 ,DAT_IN_LINE_STRIDE     )
    CSB_Write(regs, PosEmb_reg_bias+6 ,feature_out_base       )
    CSB_Write(regs, PosEmb_reg_bias+7 ,DAT_OUT_HEAD_STRIDE    )
    CSB_Write(regs, PosEmb_reg_bias+8 ,DAT_OUT_LINE_STRIDE    )
    CSB_Write(regs, PosEmb_reg_bias+9 ,CHin_div_Tout          )
    CSB_Write(regs, PosEmb_reg_bias+10,Dynamic_Token          )
    CSB_Write(regs, PosEmb_reg_bias+11,last_token             )
    CSB_Write(regs, PosEmb_reg_bias+12,Feature_Head           )
    CSB_Write(regs, PosEmb_reg_bias+13,POS_HEAD_STRIDE        )
    CSB_Write(regs, PosEmb_reg_bias+14,POS_LINE_STRIDE        )
    CSB_Write(regs, PosEmb_reg_bias+15,0                      )
    CSB_Write(regs, PosEmb_reg_bias+16,0                      )
    CSB_Write(regs, PosEmb_reg_bias+17,0b00_0100              )
    CSB_Read(regs, PosEmb_reg_bias+1,1)
    return regs


@Tasks.Register("accel.hbm.pos_emb", device.EdgeLLMv2)
def PosEmb_v2(**kwargs):
    device = kwargs["device"]
    Token = kwargs["Token"]
    kvcache = kwargs.get("kvcache", 0)
    Head = kwargs.get("Head", 1)
    Feature_Head = kwargs["Feature_Head"]
    DAT_IN_BASE_ADDR = kwargs.get("DAT_IN_BASE_ADDR")
    POS_IN_BASE_ADDR = kwargs.get("POS_IN_BASE_ADDR")
    DAT_OUT_BASE_ADDR = kwargs.get("DAT_OUT_BASE_ADDR")
    last_token = kwargs.get("last_token", 0)
    padding = kwargs.get("padding", 0)
    DAT_IN_ONCHIP = kwargs.get("DAT_IN_ONCHIP")
    DAT_OUT_ONCHIP = kwargs.get("DAT_OUT_ONCHIP")

    Tin = device.base_Tin
    Tout = device.Tout
    Pixel_Data_Bytes = device.Pixel_Data_Bytes
    WT_DW = device.MAX_WT_DW
    HBM_AXI_DATA_WIDTH = device.HBM_AXI_DATA_WIDTH
    WT_CH_Tgroup = device.WT_CH_Tgroup
    MAX_WT_DW = device.MAX_WT_DW
    MAX_BN_DW = device.MAX_BN_DW
    HBM_Port = device.HBM_Port
    WT_BRAM_DEPTH = device.WT_BRAM_DEPTH
    AXI_BN_WIDTH = device.AXI_BN_WIDTH
    BN_FIFO_DEP = device.BN_FIFO_DEP
    BN_FIFO_NUM = device.BN_FIFO_NUM
    AXI_DAT_WIDTH = device.AXI_DAT_WIDTH
    log2_AXI_BURST_LEN = device.log2_AXI_BURST_LEN
    MAX_TOKEN = device.MAX_TOKEN
    MAX_CH_per_HEAD = device.MAX_CH_per_HEAD

    Dynamic_Token = If(kvcache, 1, Token - last_token)
    Win = Dynamic_Token
    Hin = Head
    CHin = MAX_CH_per_HEAD
    CHout = CHin
    if padding:
        Wout = MAX_TOKEN
    else:
        Wout = Win
    Hout = Hin
    CHout_div_Tout = ((CHout + Tout - 1) // Tout)
    CHin_div_Tout = ((CHin + Tout - 1) // Tout)
    CHin_Padding_with_Tout = CHin_div_Tout * Tout
    LN_num_per_AXI_DW = AXI_DAT_WIDTH // (2*MAX_BN_DW)
    Tin_div_Tout = (Tin + Tout - 1) // Tout
    CHout_Padding = CHout_div_Tout * Tout
    DAT_IN_LINE_STRIDE = Pixel_Data_Bytes * Win
    DAT_IN_HEAD_STRIDE = Pixel_Data_Bytes * Win * Hin * CHin_div_Tout
    DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes * Wout
    DAT_OUT_HEAD_STRIDE = Pixel_Data_Bytes * Wout * Hout * CHout_div_Tout
    POS_HEAD_STRIDE = Pixel_Data_Bytes*MAX_TOKEN*CHin_div_Tout//2
    POS_LINE_STRIDE = Pixel_Data_Bytes*MAX_TOKEN

    ## Hardware Testbench
    PosEmb_reg_bias=192
    feature_in_base=DAT_IN_BASE_ADDR
    if padding:
        feature_out_base=If(kvcache, DAT_OUT_BASE_ADDR+(Token-1)*Pixel_Data_Bytes, DAT_OUT_BASE_ADDR+last_token*Pixel_Data_Bytes)
    else:
        feature_out_base = DAT_OUT_BASE_ADDR
    PosEmb_in_base=If(kvcache, POS_IN_BASE_ADDR+(Token-1)*Pixel_Data_Bytes, POS_IN_BASE_ADDR+last_token*Pixel_Data_Bytes)

    onchip = 0
    if DAT_IN_ONCHIP is not None:
        onchip += 0b1
        feature_in_base = If(kvcache, DAT_IN_ONCHIP, feature_in_base)
    if DAT_OUT_ONCHIP is not None:
        onchip += 0b10
        feature_out_base = If(kvcache, DAT_OUT_ONCHIP, feature_out_base)

    if onchip:
        onchip = If(kvcache, onchip, 0)
    regs = [] 
    CSB_Write(regs, PosEmb_reg_bias+2 ,PosEmb_in_base         )
    CSB_Write(regs, PosEmb_reg_bias+3 ,feature_in_base        )
    CSB_Write(regs, PosEmb_reg_bias+4 ,DAT_IN_HEAD_STRIDE     )
    CSB_Write(regs, PosEmb_reg_bias+5 ,DAT_IN_LINE_STRIDE     )
    CSB_Write(regs, PosEmb_reg_bias+6 ,feature_out_base       )
    CSB_Write(regs, PosEmb_reg_bias+7 ,DAT_OUT_HEAD_STRIDE    )
    CSB_Write(regs, PosEmb_reg_bias+8 ,DAT_OUT_LINE_STRIDE    )
    CSB_Write(regs, PosEmb_reg_bias+9 ,CHin_div_Tout          )
    CSB_Write(regs, PosEmb_reg_bias+10,Dynamic_Token          )
    CSB_Write(regs, PosEmb_reg_bias+11,last_token             )
    CSB_Write(regs, PosEmb_reg_bias+12,Feature_Head           )
    CSB_Write(regs, PosEmb_reg_bias+13,POS_HEAD_STRIDE        )
    CSB_Write(regs, PosEmb_reg_bias+14,POS_LINE_STRIDE        )
    CSB_Write(regs, PosEmb_reg_bias+15,0                      )
    CSB_Write(regs, PosEmb_reg_bias+16,onchip                 )
    CSB_Write(regs, PosEmb_reg_bias+17,0b00_0100              )
    CSB_Read(regs, PosEmb_reg_bias+1,1)
    return regs


@Tasks.Register("accel.hbm.mvm_afterTRP", device.HBM0603)
def MVM_afterTRP(**kwargs):
    device = kwargs["device"]
    Token = kwargs["Token"]
    kvcache = kwargs.get("kvcache", 0)
    Head = kwargs.get("Head", 1)
    Feature_Head = kwargs["Feature_Head"]
    Weight_Head = kwargs["Weight_Head"]
    DAT_IN_BASE_ADDR = kwargs.get("DAT_IN_BASE_ADDR")
    WT_BASE_ADDR = kwargs.get("WT_BASE_ADDR")
    DAT_OUT_BASE_ADDR = kwargs.get("DAT_OUT_BASE_ADDR")
    last_token = kwargs.get("last_token", 0)

    Tin = device.base_Tin
    Tout = device.Tout
    Pixel_Data_Bytes = device.Pixel_Data_Bytes
    WT_DW = device.MAX_WT_DW
    HBM_AXI_DATA_WIDTH = device.HBM_AXI_DATA_WIDTH
    WT_CH_Tgroup = device.WT_CH_Tgroup
    MAX_WT_DW = device.MAX_WT_DW
    MAX_BN_DW = device.MAX_BN_DW
    HBM_Port = device.HBM_Port
    WT_BRAM_DEPTH = device.WT_BRAM_DEPTH
    AXI_BN_WIDTH = device.AXI_BN_WIDTH
    BN_FIFO_DEP = device.BN_FIFO_DEP
    BN_FIFO_NUM = device.BN_FIFO_NUM
    AXI_DAT_WIDTH = device.AXI_DAT_WIDTH
    log2_AXI_BURST_LEN = device.log2_AXI_BURST_LEN
    MAX_TOKEN = device.MAX_TOKEN
    MAX_CH_per_HEAD = device.MAX_CH_per_HEAD
    MIN_WT_HEAD = device.MIN_WT_HEAD

    Dynamic_Token = If(kvcache, 1, Token - last_token)
    Win = Dynamic_Token
    Hin = Head
    CHin = MAX_CH_per_HEAD
    CHout = Token
    Wout = Win
    Hout = Hin
    CHout_div_Tout = ((CHout + Tout - 1) // Tout)
    CHin_div_Tout = ((CHin + Tout - 1) // Tout)
    CHin_Padding_with_Tout = CHin_div_Tout * Tout
    Tin_div_Tout = (Tin + Tout - 1) // Tout
    CHout_Padding = CHout_div_Tout * Tout
    DAT_IN_LINE_STRIDE = Pixel_Data_Bytes * Win
    DAT_IN_HEAD_STRIDE = Pixel_Data_Bytes * Win * Hin * CHin_div_Tout
    WET_IN_LINE_STRIDE = Pixel_Data_Bytes * MAX_TOKEN
    WET_IN_HEAD_STRIDE = Pixel_Data_Bytes * MAX_TOKEN * ((MAX_CH_per_HEAD + Tout - 1) // Tout) 
    DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes * Wout
    DAT_OUT_HEAD_STRIDE = Pixel_Data_Bytes * Wout * Hout * CHout_div_Tout

    # Hardware Testbench
    feature_in_base=DAT_IN_BASE_ADDR
    feature_out_base=DAT_OUT_BASE_ADDR
    recip_ch = np.array([1/math.sqrt(CHin),], dtype="float16")
    FP16_rsqrt = np.frombuffer(recip_ch.tobytes(), dtype="uint16")[0]
    Feature_Repeat_times_minus1=Feature_Head//MIN_WT_HEAD-1
    Head_Cfg = (Feature_Head // Weight_Head - 1) * 256 * 256 + Feature_Head * 256 + Weight_Head
    reg_bias = 192

    regs = []
    CSB_Write(regs, reg_bias+2 , WT_BASE_ADDR               )
    CSB_Write(regs, reg_bias+3 , feature_in_base            )
    CSB_Write(regs, reg_bias+4 , DAT_IN_HEAD_STRIDE         )
    CSB_Write(regs, reg_bias+5 , DAT_IN_LINE_STRIDE         )
    CSB_Write(regs, reg_bias+6 , feature_out_base           )
    CSB_Write(regs, reg_bias+7 , DAT_OUT_HEAD_STRIDE        )
    CSB_Write(regs, reg_bias+8 , DAT_OUT_LINE_STRIDE        )
    CSB_Write(regs, reg_bias+9 , WET_IN_HEAD_STRIDE         )
    CSB_Write(regs, reg_bias+10, WET_IN_LINE_STRIDE         )
    CSB_Write(regs, reg_bias+11, (CHin+Tout-1)//Tout        )
    CSB_Write(regs, reg_bias+12, Token                      )
    CSB_Write(regs, reg_bias+13, Dynamic_Token              )
    CSB_Write(regs, reg_bias+14, FP16_rsqrt                 )
    CSB_Write(regs, reg_bias+15, Head_Cfg                   )
    CSB_Write(regs, reg_bias+16, 0                          )
    CSB_Write(regs, reg_bias+17, 0b00_0010                  )
    CSB_Read(regs, reg_bias+1,1)
    return regs


@Tasks.Register("accel.hbm.mvm_afterTRP", device.HBM0720)
def MVM_afterTRP_task_0720(**kwargs):
    device = kwargs["device"]
    Token = kwargs["Token"]
    kvcache = kwargs.get("kvcache", 0)
    Head = kwargs.get("Head", 1)
    Feature_Head = kwargs["Feature_Head"]
    Weight_Head = kwargs["Weight_Head"]
    DAT_IN_BASE_ADDR = kwargs.get("DAT_IN_BASE_ADDR")
    WT_BASE_ADDR = kwargs.get("WT_BASE_ADDR")
    DAT_OUT_BASE_ADDR = kwargs.get("DAT_OUT_BASE_ADDR")
    last_token = kwargs.get("last_token", 0)

    Tin = device.base_Tin
    Tout = device.Tout
    Pixel_Data_Bytes = device.Pixel_Data_Bytes
    WT_DW = device.MAX_WT_DW
    HBM_AXI_DATA_WIDTH = device.HBM_AXI_DATA_WIDTH
    WT_CH_Tgroup = device.WT_CH_Tgroup
    MAX_WT_DW = device.MAX_WT_DW
    MAX_BN_DW = device.MAX_BN_DW
    HBM_Port = device.HBM_Port
    WT_BRAM_DEPTH = device.WT_BRAM_DEPTH
    AXI_BN_WIDTH = device.AXI_BN_WIDTH
    BN_FIFO_DEP = device.BN_FIFO_DEP
    BN_FIFO_NUM = device.BN_FIFO_NUM
    AXI_DAT_WIDTH = device.AXI_DAT_WIDTH
    log2_AXI_BURST_LEN = device.log2_AXI_BURST_LEN
    MAX_TOKEN = device.MAX_TOKEN
    MAX_CH_per_HEAD = device.MAX_CH_per_HEAD
    MIN_WT_HEAD = device.MIN_WT_HEAD

    Dynamic_Token = If(kvcache, 1, Token - last_token)
    Win = Dynamic_Token
    Hin = Head
    CHin = MAX_CH_per_HEAD
    CHout = Token
    Wout = Win
    Hout = Hin
    CHout_div_Tout = ((CHout + Tout - 1) // Tout)
    CHin_div_Tout = ((CHin + Tout - 1) // Tout)
    CHin_Padding_with_Tout = CHin_div_Tout * Tout
    Tin_div_Tout = (Tin + Tout - 1) // Tout
    CHout_Padding = CHout_div_Tout * Tout
    DAT_IN_LINE_STRIDE = Pixel_Data_Bytes * Win
    DAT_IN_HEAD_STRIDE = Pixel_Data_Bytes * Win * Hin * CHin_div_Tout
    WET_IN_LINE_STRIDE = Pixel_Data_Bytes * MAX_TOKEN
    WET_IN_HEAD_STRIDE = Pixel_Data_Bytes * MAX_TOKEN * ((MAX_CH_per_HEAD + Tout - 1) // Tout) 
    DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes * Wout
    DAT_OUT_HEAD_STRIDE = Pixel_Data_Bytes * Wout * Hout * CHout_div_Tout

    # Hardware Testbench
    feature_in_base=DAT_IN_BASE_ADDR
    feature_out_base=DAT_OUT_BASE_ADDR
    recip_ch = np.array([1/math.sqrt(CHin),], dtype="float16")
    FP16_rsqrt = np.frombuffer(recip_ch.tobytes(), dtype="uint16")[0]
    Feature_Repeat_times_minus1=Feature_Head//MIN_WT_HEAD-1
    Head_Cfg = (Feature_Head // Weight_Head - 1) * 256 * 256 + Feature_Head * 256 + Weight_Head
    reg_bias = 192

    regs = []
    CSB_Write(regs, reg_bias+2 , WT_BASE_ADDR               )
    CSB_Write(regs, reg_bias+3 , feature_in_base            )
    CSB_Write(regs, reg_bias+4 , DAT_IN_HEAD_STRIDE         )
    CSB_Write(regs, reg_bias+5 , DAT_IN_LINE_STRIDE         )
    CSB_Write(regs, reg_bias+6 , feature_out_base           )
    CSB_Write(regs, reg_bias+7 , DAT_OUT_HEAD_STRIDE        )
    CSB_Write(regs, reg_bias+8 , DAT_OUT_LINE_STRIDE        )
    CSB_Write(regs, reg_bias+9 , WET_IN_HEAD_STRIDE         )
    CSB_Write(regs, reg_bias+10, WET_IN_LINE_STRIDE         )
    CSB_Write(regs, reg_bias+11, CHin                       )
    CSB_Write(regs, reg_bias+12, Token                      )
    CSB_Write(regs, reg_bias+13, Dynamic_Token              )
    CSB_Write(regs, reg_bias+14, FP16_rsqrt                 )
    CSB_Write(regs, reg_bias+15, Head_Cfg                   )
    CSB_Write(regs, reg_bias+16, 0                          )
    CSB_Write(regs, reg_bias+17, 0b00_0010                  )
    CSB_Read(regs, reg_bias+1,1)
    return regs


@Tasks.Register("accel.hbm.mvm_afterTRP", device.EdgeLLMv2)
def MVM_afterTRP_task_0824(**kwargs):
    device = kwargs["device"]
    Token = kwargs["Token"]
    kvcache = kwargs.get("kvcache", 0)
    Head = kwargs.get("Head", 1)
    Feature_Head = kwargs["Feature_Head"]
    Weight_Head = kwargs["Weight_Head"]
    DAT_IN_BASE_ADDR = kwargs.get("DAT_IN_BASE_ADDR")
    WT_BASE_ADDR = kwargs.get("WT_BASE_ADDR")
    DAT_OUT_BASE_ADDR = kwargs.get("DAT_OUT_BASE_ADDR")
    last_token = kwargs.get("last_token", 0)
    DAT_IN_ONCHIP = kwargs.get("DAT_IN_ONCHIP")
    DAT_OUT_ONCHIP = kwargs.get("DAT_OUT_ONCHIP")

    Tin = device.base_Tin
    Tout = device.Tout
    Pixel_Data_Bytes = device.Pixel_Data_Bytes
    WT_DW = device.MAX_WT_DW
    HBM_AXI_DATA_WIDTH = device.HBM_AXI_DATA_WIDTH
    WT_CH_Tgroup = device.WT_CH_Tgroup
    MAX_WT_DW = device.MAX_WT_DW
    MAX_BN_DW = device.MAX_BN_DW
    HBM_Port = device.HBM_Port
    WT_BRAM_DEPTH = device.WT_BRAM_DEPTH
    AXI_BN_WIDTH = device.AXI_BN_WIDTH
    BN_FIFO_DEP = device.BN_FIFO_DEP
    BN_FIFO_NUM = device.BN_FIFO_NUM
    AXI_DAT_WIDTH = device.AXI_DAT_WIDTH
    log2_AXI_BURST_LEN = device.log2_AXI_BURST_LEN
    MAX_TOKEN = device.MAX_TOKEN
    MAX_CH_per_HEAD = device.MAX_CH_per_HEAD
    MIN_WT_HEAD = device.MIN_WT_HEAD

    Dynamic_Token = If(kvcache, 1, Token - last_token)
    Win = Dynamic_Token
    Hin = Head
    CHin = MAX_CH_per_HEAD
    CHout = Token
    Wout = Win
    Hout = Hin
    CHout_div_Tout = ((CHout + Tout - 1) // Tout)
    CHin_div_Tout = ((CHin + Tout - 1) // Tout)
    CHin_Padding_with_Tout = CHin_div_Tout * Tout
    Tin_div_Tout = (Tin + Tout - 1) // Tout
    CHout_Padding = CHout_div_Tout * Tout
    DAT_IN_LINE_STRIDE = Pixel_Data_Bytes * Win
    DAT_IN_HEAD_STRIDE = Pixel_Data_Bytes * Win * Hin * CHin_div_Tout
    WET_IN_LINE_STRIDE = Pixel_Data_Bytes * MAX_TOKEN
    WET_IN_HEAD_STRIDE = Pixel_Data_Bytes * MAX_TOKEN * ((MAX_CH_per_HEAD + Tout - 1) // Tout) 
    DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes * Wout
    DAT_OUT_HEAD_STRIDE = Pixel_Data_Bytes * Wout * Hout * CHout_div_Tout

    # Hardware Testbench
    feature_in_base=DAT_IN_BASE_ADDR
    feature_out_base=DAT_OUT_BASE_ADDR
    recip_ch = np.array([1/math.sqrt(CHin),], dtype="float16")
    FP16_rsqrt = np.frombuffer(recip_ch.tobytes(), dtype="uint16")[0]
    Feature_Repeat_times_minus1=Feature_Head//MIN_WT_HEAD-1
    Head_Cfg = (Feature_Head // Weight_Head - 1) * 256 * 256 + Feature_Head * 256 + Weight_Head
    reg_bias = 192

    onchip = 0
    if DAT_IN_ONCHIP is not None:
        onchip += 0b1
        feature_in_base = If(kvcache, DAT_IN_ONCHIP, feature_in_base)
    if DAT_OUT_ONCHIP is not None:
        onchip += 0b10
        feature_out_base = If(kvcache, DAT_OUT_ONCHIP, feature_out_base)

    if onchip:
        onchip = If(kvcache, onchip, 0)
    regs = []
    CSB_Write(regs, reg_bias+2 , WT_BASE_ADDR               )
    CSB_Write(regs, reg_bias+3 , feature_in_base            )
    CSB_Write(regs, reg_bias+4 , DAT_IN_HEAD_STRIDE         )
    CSB_Write(regs, reg_bias+5 , DAT_IN_LINE_STRIDE         )
    CSB_Write(regs, reg_bias+6 , feature_out_base           )
    CSB_Write(regs, reg_bias+7 , DAT_OUT_HEAD_STRIDE        )
    CSB_Write(regs, reg_bias+8 , DAT_OUT_LINE_STRIDE        )
    CSB_Write(regs, reg_bias+9 , WET_IN_HEAD_STRIDE         )
    CSB_Write(regs, reg_bias+10, WET_IN_LINE_STRIDE         )
    CSB_Write(regs, reg_bias+11, CHin                       )
    CSB_Write(regs, reg_bias+12, Token                      )
    CSB_Write(regs, reg_bias+13, Dynamic_Token              )
    CSB_Write(regs, reg_bias+14, FP16_rsqrt                 )
    CSB_Write(regs, reg_bias+15, Head_Cfg                   )
    CSB_Write(regs, reg_bias+16, onchip                          )
    CSB_Write(regs, reg_bias+17, 0b00_0010                  )
    CSB_Read(regs, reg_bias+1,1)
    return regs


@Tasks.Register("accel.hbm.mvm_afterF2W", device.HBM0603)
def MVM_afterF2W(**kwargs):
    device = kwargs["device"]
    Token = kwargs["Token"]
    kvcache = kwargs.get("kvcache", 0)
    Head = kwargs.get("Head", 1)
    Feature_Head = kwargs["Feature_Head"]
    Weight_Head = kwargs["Weight_Head"]
    DAT_IN_BASE_ADDR = kwargs.get("DAT_IN_BASE_ADDR")
    WT_BASE_ADDR = kwargs.get("WT_BASE_ADDR")
    DAT_OUT_BASE_ADDR = kwargs.get("DAT_OUT_BASE_ADDR")
    last_token = kwargs.get("last_token", 0)

    Tin = device.base_Tin
    Tout = device.Tout
    Pixel_Data_Bytes = device.Pixel_Data_Bytes
    WT_DW = device.MAX_WT_DW
    HBM_AXI_DATA_WIDTH = device.HBM_AXI_DATA_WIDTH
    WT_CH_Tgroup = device.WT_CH_Tgroup
    MAX_WT_DW = device.MAX_WT_DW
    MAX_BN_DW = device.MAX_BN_DW
    HBM_Port = device.HBM_Port
    WT_BRAM_DEPTH = device.WT_BRAM_DEPTH
    AXI_BN_WIDTH = device.AXI_BN_WIDTH
    BN_FIFO_DEP = device.BN_FIFO_DEP
    BN_FIFO_NUM = device.BN_FIFO_NUM
    AXI_DAT_WIDTH = device.AXI_DAT_WIDTH
    log2_AXI_BURST_LEN = device.log2_AXI_BURST_LEN
    MAX_TOKEN = device.MAX_TOKEN
    MAX_CH_per_HEAD = device.MAX_CH_per_HEAD
    MIN_WT_HEAD = device.MIN_WT_HEAD

    Win = If(kvcache, 1, Token - last_token)
    Hin = Head
    CHin = Token
    CHout = MAX_CH_per_HEAD
    Wout = If(kvcache, 1, Token - last_token)
    Hout = Hin
    CHout_div_Tout = ((CHout + Tout - 1) // Tout)
    CHin_div_Tout = ((CHin + Tout - 1) // Tout)
    CHin_Padding_with_Tout = CHin_div_Tout * Tout
    Tin_div_Tout = (Tin + Tout - 1) // Tout
    CHout_Padding = CHout_div_Tout * Tout
    DAT_IN_LINE_STRIDE = Pixel_Data_Bytes * Win
    DAT_IN_HEAD_STRIDE = Pixel_Data_Bytes * Win * Hin * CHin_div_Tout
    WET_IN_LINE_STRIDE = Pixel_Data_Bytes * MAX_TOKEN
    WET_IN_HEAD_STRIDE = Pixel_Data_Bytes * MAX_TOKEN * CHout_div_Tout
    DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes * Wout
    DAT_OUT_HEAD_STRIDE = Pixel_Data_Bytes * Wout * Hout * CHout_div_Tout

    # Hardware Testbench
    feature_in_base=DAT_IN_BASE_ADDR
    feature_out_base=DAT_OUT_BASE_ADDR
    Dynamic_Token = If(kvcache, 1, Token - last_token)
    Feature_Repeat_times_minus1=Feature_Head//MIN_WT_HEAD-1
    Head_Cfg = (Feature_Head // Weight_Head - 1) * 256 * 256 + Feature_Head * 256 + Weight_Head
    reg_bias = 192

    regs = []
    CSB_Write(regs, reg_bias+2 , WT_BASE_ADDR               )
    CSB_Write(regs, reg_bias+3 , feature_in_base            )
    CSB_Write(regs, reg_bias+4 , DAT_IN_HEAD_STRIDE         )
    CSB_Write(regs, reg_bias+5 , DAT_IN_LINE_STRIDE         )
    CSB_Write(regs, reg_bias+6 , feature_out_base           )
    CSB_Write(regs, reg_bias+7 , DAT_OUT_HEAD_STRIDE        )
    CSB_Write(regs, reg_bias+8 , DAT_OUT_LINE_STRIDE        )
    CSB_Write(regs, reg_bias+9 , WET_IN_HEAD_STRIDE         )
    CSB_Write(regs, reg_bias+10, WET_IN_LINE_STRIDE         )
    CSB_Write(regs, reg_bias+11, (Token+Tout-1)//Tout       )
    CSB_Write(regs, reg_bias+12, Token                      )
    CSB_Write(regs, reg_bias+13, Dynamic_Token              )
    CSB_Write(regs, reg_bias+14, (CHout+Tout-1)//Tout       ) # CHout_div_Tout
    CSB_Write(regs, reg_bias+15, Head_Cfg                   )
    CSB_Write(regs, reg_bias+16, 0                          )
    CSB_Write(regs, reg_bias+17, 0b00_0001                  )
    CSB_Read(regs, reg_bias+1,1)
    return regs


@Tasks.Register("accel.hbm.mvm_afterF2W", device.HBM0720)
def MVM_afterF2W_Task_0724(**kwargs):
    device = kwargs["device"]
    Token = kwargs["Token"]
    kvcache = kwargs.get("kvcache", 0)
    Head = kwargs.get("Head", 1)
    Feature_Head = kwargs["Feature_Head"]
    Weight_Head = kwargs["Weight_Head"]
    DAT_IN_BASE_ADDR = kwargs.get("DAT_IN_BASE_ADDR")
    WT_BASE_ADDR = kwargs.get("WT_BASE_ADDR")
    DAT_OUT_BASE_ADDR = kwargs.get("DAT_OUT_BASE_ADDR")
    last_token = kwargs.get("last_token", 0)

    Tin = device.base_Tin
    Tout = device.Tout
    Pixel_Data_Bytes = device.Pixel_Data_Bytes
    WT_DW = device.MAX_WT_DW
    HBM_AXI_DATA_WIDTH = device.HBM_AXI_DATA_WIDTH
    WT_CH_Tgroup = device.WT_CH_Tgroup
    MAX_WT_DW = device.MAX_WT_DW
    MAX_BN_DW = device.MAX_BN_DW
    HBM_Port = device.HBM_Port
    WT_BRAM_DEPTH = device.WT_BRAM_DEPTH
    AXI_BN_WIDTH = device.AXI_BN_WIDTH
    BN_FIFO_DEP = device.BN_FIFO_DEP
    BN_FIFO_NUM = device.BN_FIFO_NUM
    AXI_DAT_WIDTH = device.AXI_DAT_WIDTH
    log2_AXI_BURST_LEN = device.log2_AXI_BURST_LEN
    MAX_TOKEN = device.MAX_TOKEN
    MAX_CH_per_HEAD = device.MAX_CH_per_HEAD
    MIN_WT_HEAD = device.MIN_WT_HEAD

    Win = If(kvcache, 1, Token - last_token)
    Hin = Head
    CHin = Token
    CHout = MAX_CH_per_HEAD
    Wout = If(kvcache, 1, Token - last_token)
    Hout = Hin
    CHout_div_Tout = ((CHout + Tout - 1) // Tout)
    CHin_div_Tout = ((CHin + Tout - 1) // Tout)
    CHin_Padding_with_Tout = CHin_div_Tout * Tout
    Tin_div_Tout = (Tin + Tout - 1) // Tout
    CHout_Padding = CHout_div_Tout * Tout
    DAT_IN_LINE_STRIDE = Pixel_Data_Bytes * Win
    DAT_IN_HEAD_STRIDE = Pixel_Data_Bytes * Win * Hin * CHin_div_Tout
    WET_IN_LINE_STRIDE = Pixel_Data_Bytes * MAX_TOKEN
    WET_IN_HEAD_STRIDE = Pixel_Data_Bytes * MAX_TOKEN * CHout_div_Tout
    DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes * Wout
    DAT_OUT_HEAD_STRIDE = Pixel_Data_Bytes * Wout * Hout * CHout_div_Tout

    # Hardware Testbench
    feature_in_base=DAT_IN_BASE_ADDR
    feature_out_base=DAT_OUT_BASE_ADDR
    Dynamic_Token = If(kvcache, 1, Token - last_token)
    Feature_Repeat_times_minus1=Feature_Head//MIN_WT_HEAD-1
    Head_Cfg = (Feature_Head // Weight_Head - 1) * 256 * 256 + Feature_Head * 256 + Weight_Head
    reg_bias = 192

    regs = []
    CSB_Write(regs, reg_bias+2 , WT_BASE_ADDR               )
    CSB_Write(regs, reg_bias+3 , feature_in_base            )
    CSB_Write(regs, reg_bias+4 , DAT_IN_HEAD_STRIDE         )
    CSB_Write(regs, reg_bias+5 , DAT_IN_LINE_STRIDE         )
    CSB_Write(regs, reg_bias+6 , feature_out_base           )
    CSB_Write(regs, reg_bias+7 , DAT_OUT_HEAD_STRIDE        )
    CSB_Write(regs, reg_bias+8 , DAT_OUT_LINE_STRIDE        )
    CSB_Write(regs, reg_bias+9 , WET_IN_HEAD_STRIDE         )
    CSB_Write(regs, reg_bias+10, WET_IN_LINE_STRIDE         )
    CSB_Write(regs, reg_bias+11, Token                      )
    CSB_Write(regs, reg_bias+12, Token                      )
    CSB_Write(regs, reg_bias+13, Dynamic_Token              )
    CSB_Write(regs, reg_bias+14, (CHout+Tout-1)//Tout       ) # CHout_div_Tout
    CSB_Write(regs, reg_bias+15, Head_Cfg                   )
    CSB_Write(regs, reg_bias+16, 0                          )
    CSB_Write(regs, reg_bias+17, 0b00_0001                  )
    CSB_Read(regs, reg_bias+1,1)
    return regs


@Tasks.Register("accel.hbm.mvm_afterF2W", device.EdgeLLMv1)
def MVM_afterF2W_Task_0725(**kwargs):
    device = kwargs["device"]
    Token = kwargs["Token"]
    kvcache = kwargs.get("kvcache", 0)
    Head = kwargs.get("Head", 1)
    Feature_Head = kwargs["Feature_Head"]
    Weight_Head = kwargs["Weight_Head"]
    DAT_IN_BASE_ADDR = kwargs.get("DAT_IN_BASE_ADDR")
    WT_BASE_ADDR = kwargs.get("WT_BASE_ADDR")
    DAT_OUT_BASE_ADDR = kwargs.get("DAT_OUT_BASE_ADDR")
    last_token = kwargs.get("last_token", 0)

    Tin = device.base_Tin
    Tout = device.Tout
    Pixel_Data_Bytes = device.Pixel_Data_Bytes
    WT_DW = device.MAX_WT_DW
    HBM_AXI_DATA_WIDTH = device.HBM_AXI_DATA_WIDTH
    WT_CH_Tgroup = device.WT_CH_Tgroup
    MAX_WT_DW = device.MAX_WT_DW
    MAX_BN_DW = device.MAX_BN_DW
    HBM_Port = device.HBM_Port
    WT_BRAM_DEPTH = device.WT_BRAM_DEPTH
    AXI_BN_WIDTH = device.AXI_BN_WIDTH
    BN_FIFO_DEP = device.BN_FIFO_DEP
    BN_FIFO_NUM = device.BN_FIFO_NUM
    AXI_DAT_WIDTH = device.AXI_DAT_WIDTH
    log2_AXI_BURST_LEN = device.log2_AXI_BURST_LEN
    MAX_TOKEN = device.MAX_TOKEN
    MAX_CH_per_HEAD = device.MAX_CH_per_HEAD
    MIN_WT_HEAD = device.MIN_WT_HEAD

    Win = If(kvcache, 1, Token - last_token)
    Hin = Head
    CHin = Token
    CHout = MAX_CH_per_HEAD
    Wout = If(kvcache, 1, Token - last_token)
    Hout = Hin
    CHout_div_Tout = ((CHout + Tout - 1) // Tout)
    CHin_div_Tout = ((CHin + Tout - 1) // Tout)
    CHin_Padding_with_Tout = CHin_div_Tout * Tout
    Tin_div_Tout = (Tin + Tout - 1) // Tout
    CHout_Padding = CHout_div_Tout * Tout
    DAT_IN_LINE_STRIDE = Pixel_Data_Bytes * Win
    DAT_IN_HEAD_STRIDE = Pixel_Data_Bytes * Win * Hin * CHin_div_Tout
    WET_IN_LINE_STRIDE = Pixel_Data_Bytes * MAX_TOKEN
    WET_IN_HEAD_STRIDE = Pixel_Data_Bytes * MAX_TOKEN * CHout_div_Tout
    DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes * Wout
    DAT_OUT_HEAD_STRIDE = Pixel_Data_Bytes * Wout * Hout * CHout_div_Tout

    # Hardware Testbench
    feature_in_base=DAT_IN_BASE_ADDR
    feature_out_base=DAT_OUT_BASE_ADDR
    Dynamic_Token = If(kvcache, 1, Token - last_token)
    Feature_Repeat_times_minus1=Feature_Head//MIN_WT_HEAD-1
    Head_Cfg = (Feature_Head // Weight_Head - 1) * 256 * 256 + Feature_Head * 256 + Weight_Head
    reg_bias = 192

    regs = []
    CSB_Write(regs, reg_bias+2 , WT_BASE_ADDR               )
    CSB_Write(regs, reg_bias+3 , feature_in_base            )
    CSB_Write(regs, reg_bias+4 , DAT_IN_HEAD_STRIDE         )
    CSB_Write(regs, reg_bias+5 , DAT_IN_LINE_STRIDE         )
    CSB_Write(regs, reg_bias+6 , feature_out_base           )
    CSB_Write(regs, reg_bias+7 , DAT_OUT_HEAD_STRIDE        )
    CSB_Write(regs, reg_bias+8 , DAT_OUT_LINE_STRIDE        )
    CSB_Write(regs, reg_bias+9 , WET_IN_HEAD_STRIDE         )
    CSB_Write(regs, reg_bias+10, WET_IN_LINE_STRIDE         )
    CSB_Write(regs, reg_bias+11, CHout                      )
    CSB_Write(regs, reg_bias+12, Token                      )
    CSB_Write(regs, reg_bias+13, Dynamic_Token              )
    CSB_Write(regs, reg_bias+14, 0x3c00                     )
    CSB_Write(regs, reg_bias+15, Head_Cfg                   )
    CSB_Write(regs, reg_bias+16, 0                          )
    CSB_Write(regs, reg_bias+17, 0b00_0001                  )
    CSB_Read(regs, reg_bias+1,1)
    return regs


@Tasks.Register("accel.hbm.mvm_afterF2W", device.EdgeLLMv2)
def MVM_afterF2W_Task_0824(**kwargs):
    device = kwargs["device"]
    Token = kwargs["Token"]
    kvcache = kwargs.get("kvcache", 0)
    Head = kwargs.get("Head", 1)
    Feature_Head = kwargs["Feature_Head"]
    Weight_Head = kwargs["Weight_Head"]
    DAT_IN_BASE_ADDR = kwargs.get("DAT_IN_BASE_ADDR")
    WT_BASE_ADDR = kwargs.get("WT_BASE_ADDR")
    DAT_OUT_BASE_ADDR = kwargs.get("DAT_OUT_BASE_ADDR")
    last_token = kwargs.get("last_token", 0)
    DAT_IN_ONCHIP = kwargs.get("DAT_IN_ONCHIP")
    DAT_OUT_ONCHIP = kwargs.get("DAT_OUT_ONCHIP")

    Tin = device.base_Tin
    Tout = device.Tout
    Pixel_Data_Bytes = device.Pixel_Data_Bytes
    WT_DW = device.MAX_WT_DW
    HBM_AXI_DATA_WIDTH = device.HBM_AXI_DATA_WIDTH
    WT_CH_Tgroup = device.WT_CH_Tgroup
    MAX_WT_DW = device.MAX_WT_DW
    MAX_BN_DW = device.MAX_BN_DW
    HBM_Port = device.HBM_Port
    WT_BRAM_DEPTH = device.WT_BRAM_DEPTH
    AXI_BN_WIDTH = device.AXI_BN_WIDTH
    BN_FIFO_DEP = device.BN_FIFO_DEP
    BN_FIFO_NUM = device.BN_FIFO_NUM
    AXI_DAT_WIDTH = device.AXI_DAT_WIDTH
    log2_AXI_BURST_LEN = device.log2_AXI_BURST_LEN
    MAX_TOKEN = device.MAX_TOKEN
    MAX_CH_per_HEAD = device.MAX_CH_per_HEAD
    MIN_WT_HEAD = device.MIN_WT_HEAD

    Win = If(kvcache, 1, Token - last_token)
    Hin = Head
    CHin = Token
    CHout = MAX_CH_per_HEAD
    Wout = If(kvcache, 1, Token - last_token)
    Hout = Hin
    CHout_div_Tout = ((CHout + Tout - 1) // Tout)
    CHin_div_Tout = ((CHin + Tout - 1) // Tout)
    CHin_Padding_with_Tout = CHin_div_Tout * Tout
    Tin_div_Tout = (Tin + Tout - 1) // Tout
    CHout_Padding = CHout_div_Tout * Tout
    DAT_IN_LINE_STRIDE = Pixel_Data_Bytes * Win
    DAT_IN_HEAD_STRIDE = Pixel_Data_Bytes * Win * Hin * CHin_div_Tout
    WET_IN_LINE_STRIDE = Pixel_Data_Bytes * MAX_TOKEN
    WET_IN_HEAD_STRIDE = Pixel_Data_Bytes * MAX_TOKEN * CHout_div_Tout
    DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes * Wout
    DAT_OUT_HEAD_STRIDE = Pixel_Data_Bytes * Wout * Hout * CHout_div_Tout

    # Hardware Testbench
    feature_in_base=DAT_IN_BASE_ADDR
    feature_out_base=DAT_OUT_BASE_ADDR
    Dynamic_Token = If(kvcache, 1, Token - last_token)
    Feature_Repeat_times_minus1=Feature_Head//MIN_WT_HEAD-1
    Head_Cfg = (Feature_Head // Weight_Head - 1) * 256 * 256 + Feature_Head * 256 + Weight_Head
    reg_bias = 192

    onchip = 0
    if DAT_IN_ONCHIP is not None:
        onchip += 0b1
        feature_in_base = If(kvcache, DAT_IN_ONCHIP, feature_in_base)
    if DAT_OUT_ONCHIP is not None:
        onchip += 0b10
        feature_out_base = If(kvcache, DAT_OUT_ONCHIP, feature_out_base)

    if onchip:
        onchip = If(kvcache, onchip, 0)
    regs = []
    CSB_Write(regs, reg_bias+2 , WT_BASE_ADDR               )
    CSB_Write(regs, reg_bias+3 , feature_in_base            )
    CSB_Write(regs, reg_bias+4 , DAT_IN_HEAD_STRIDE         )
    CSB_Write(regs, reg_bias+5 , DAT_IN_LINE_STRIDE         )
    CSB_Write(regs, reg_bias+6 , feature_out_base           )
    CSB_Write(regs, reg_bias+7 , DAT_OUT_HEAD_STRIDE        )
    CSB_Write(regs, reg_bias+8 , DAT_OUT_LINE_STRIDE        )
    CSB_Write(regs, reg_bias+9 , WET_IN_HEAD_STRIDE         )
    CSB_Write(regs, reg_bias+10, WET_IN_LINE_STRIDE         )
    CSB_Write(regs, reg_bias+11, CHout                      )
    CSB_Write(regs, reg_bias+12, Token                      )
    CSB_Write(regs, reg_bias+13, Dynamic_Token              )
    CSB_Write(regs, reg_bias+14, 0x3c00                     )
    CSB_Write(regs, reg_bias+15, Head_Cfg                   )
    CSB_Write(regs, reg_bias+16, 0                          )
    CSB_Write(regs, reg_bias+17, 0b00_0001                  )
    CSB_Read(regs, reg_bias+1,1)
    return regs


@Tasks.Register("accel.hbm.softmax", device.HBM0603)
def Softmax(**kwargs):
    device = kwargs["device"]
    Token = kwargs["Token"]
    kvcache = kwargs.get("kvcache", 0)
    Head = kwargs.get("Head", 1)
    Feature_Head = kwargs["Feature_Head"]
    DAT_IN_BASE_ADDR = kwargs.get("DAT_IN_BASE_ADDR")
    DAT_OUT_BASE_ADDR = kwargs.get("DAT_OUT_BASE_ADDR")
    last_token = kwargs.get("last_token", 0)

    Tin = device.base_Tin
    Tout = device.Tout
    Pixel_Data_Bytes = device.Pixel_Data_Bytes
    WT_DW = device.MAX_WT_DW
    HBM_AXI_DATA_WIDTH = device.HBM_AXI_DATA_WIDTH
    WT_CH_Tgroup = device.WT_CH_Tgroup
    MAX_WT_DW = device.MAX_WT_DW
    MAX_BN_DW = device.MAX_BN_DW
    HBM_Port = device.HBM_Port
    WT_BRAM_DEPTH = device.WT_BRAM_DEPTH
    AXI_BN_WIDTH = device.AXI_BN_WIDTH
    BN_FIFO_DEP = device.BN_FIFO_DEP
    BN_FIFO_NUM = device.BN_FIFO_NUM
    AXI_DAT_WIDTH = device.AXI_DAT_WIDTH
    log2_AXI_BURST_LEN = device.log2_AXI_BURST_LEN
    MAX_TOKEN = device.MAX_TOKEN
    MAX_CH_per_HEAD = device.MAX_CH_per_HEAD
    MIN_WT_HEAD = device.MIN_WT_HEAD
    AXI_BURST_LEN_SOFTMAX = device.AXI_BURST_LEN_SOFTMAX

    Dynamic_Token = If(kvcache, 1, Token-last_token)
    Need_Mask = 1 - kvcache
    Win = Dynamic_Token
    Hin = Head
    CHin = Token
    CHout = CHin
    Wout = Win
    Hout = Hin
    CHout_div_Tout = ((CHout + Tout - 1) // Tout)
    CHin_div_Tout = ((CHin + Tout - 1) // Tout)
    CHin_Padding_with_Tout = CHin_div_Tout * Tout
    Tin_div_Tout = (Tin + Tout - 1) // Tout
    CHout_Padding = CHout_div_Tout * Tout
    DAT_IN_LINE_STRIDE = Pixel_Data_Bytes * Win
    DAT_IN_HEAD_STRIDE = Pixel_Data_Bytes * Win * Hin * CHin_div_Tout
    DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes * Wout
    DAT_OUT_HEAD_STRIDE = Pixel_Data_Bytes * Wout * Hout * CHout_div_Tout

    # Hardware Testbench
    # feature_in_base=If(kvcache, DAT_IN_BASE_ADDR+(Token-1)*Pixel_Data_Bytes, DAT_IN_BASE_ADDR+last_token*Pixel_Data_Bytes)
    # feature_out_base=If(kvcache, DAT_OUT_BASE_ADDR+(Token-1)*Pixel_Data_Bytes, DAT_OUT_BASE_ADDR+last_token*Pixel_Data_Bytes)
    feature_in_base = DAT_IN_BASE_ADDR
    feature_out_base = DAT_OUT_BASE_ADDR
    reg_bias = 192
    regs = []
    CSB_Write(regs, reg_bias+2 , Need_Mask                  )
    CSB_Write(regs, reg_bias+3 , feature_in_base            )
    CSB_Write(regs, reg_bias+4 , DAT_IN_HEAD_STRIDE         )
    CSB_Write(regs, reg_bias+5 , DAT_IN_LINE_STRIDE         )
    CSB_Write(regs, reg_bias+6 , feature_out_base           )
    CSB_Write(regs, reg_bias+7 , DAT_OUT_HEAD_STRIDE        )
    CSB_Write(regs, reg_bias+8 , DAT_OUT_LINE_STRIDE        )
    CSB_Write(regs, reg_bias+9 , (Token+Tout-1)//Tout       )
    CSB_Write(regs, reg_bias+10, Feature_Head               )
    CSB_Write(regs, reg_bias+11, Token                      )
    CSB_Write(regs, reg_bias+12, Dynamic_Token              )
    CSB_Write(regs, reg_bias+13, Token - last_token         )
    CSB_Write(regs, reg_bias+14, last_token                 )
    CSB_Write(regs, reg_bias+15, 0                          )
    CSB_Write(regs, reg_bias+16, 0                          )
    CSB_Write(regs, reg_bias+17, 0b00_1000                  )
    CSB_Read(regs, reg_bias+1,1)
    return regs


@Tasks.Register("accel.hbm.softmax", device.EdgeLLMv2)
def Softmax_v2(**kwargs):
    device = kwargs["device"]
    Token = kwargs["Token"]
    kvcache = kwargs.get("kvcache", 0)
    Head = kwargs.get("Head", 1)
    Feature_Head = kwargs["Feature_Head"]
    DAT_IN_BASE_ADDR = kwargs.get("DAT_IN_BASE_ADDR")
    DAT_OUT_BASE_ADDR = kwargs.get("DAT_OUT_BASE_ADDR")
    last_token = kwargs.get("last_token", 0)
    DAT_IN_ONCHIP = kwargs.get("DAT_IN_ONCHIP")
    DAT_OUT_ONCHIP = kwargs.get("DAT_OUT_ONCHIP")

    Tin = device.base_Tin
    Tout = device.Tout
    Pixel_Data_Bytes = device.Pixel_Data_Bytes
    WT_DW = device.MAX_WT_DW
    HBM_AXI_DATA_WIDTH = device.HBM_AXI_DATA_WIDTH
    WT_CH_Tgroup = device.WT_CH_Tgroup
    MAX_WT_DW = device.MAX_WT_DW
    MAX_BN_DW = device.MAX_BN_DW
    HBM_Port = device.HBM_Port
    WT_BRAM_DEPTH = device.WT_BRAM_DEPTH
    AXI_BN_WIDTH = device.AXI_BN_WIDTH
    BN_FIFO_DEP = device.BN_FIFO_DEP
    BN_FIFO_NUM = device.BN_FIFO_NUM
    AXI_DAT_WIDTH = device.AXI_DAT_WIDTH
    log2_AXI_BURST_LEN = device.log2_AXI_BURST_LEN
    MAX_TOKEN = device.MAX_TOKEN
    MAX_CH_per_HEAD = device.MAX_CH_per_HEAD
    MIN_WT_HEAD = device.MIN_WT_HEAD
    AXI_BURST_LEN_SOFTMAX = device.AXI_BURST_LEN_SOFTMAX

    Dynamic_Token = If(kvcache, 1, Token-last_token)
    Need_Mask = 1 - kvcache
    Win = Dynamic_Token
    Hin = Head
    CHin = Token
    CHout = CHin
    Wout = Win
    Hout = Hin
    CHout_div_Tout = ((CHout + Tout - 1) // Tout)
    CHin_div_Tout = ((CHin + Tout - 1) // Tout)
    CHin_Padding_with_Tout = CHin_div_Tout * Tout
    Tin_div_Tout = (Tin + Tout - 1) // Tout
    CHout_Padding = CHout_div_Tout * Tout
    DAT_IN_LINE_STRIDE = Pixel_Data_Bytes * Win
    DAT_IN_HEAD_STRIDE = Pixel_Data_Bytes * Win * Hin * CHin_div_Tout
    DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes * Wout
    DAT_OUT_HEAD_STRIDE = Pixel_Data_Bytes * Wout * Hout * CHout_div_Tout

    # Hardware Testbench
    feature_in_base = DAT_IN_BASE_ADDR
    feature_out_base = DAT_OUT_BASE_ADDR
    reg_bias = 192
    onchip = 0
    if DAT_IN_ONCHIP is not None:
        onchip += 0b1
        feature_in_base = If(kvcache, DAT_IN_ONCHIP, feature_in_base)
    if DAT_OUT_ONCHIP is not None:
        onchip += 0b10
        feature_out_base = If(kvcache, DAT_OUT_ONCHIP, feature_out_base)

    if onchip:
        onchip = If(kvcache, onchip, 0)
    regs = []
    CSB_Write(regs, reg_bias+2 , Need_Mask                  )
    CSB_Write(regs, reg_bias+3 , feature_in_base            )
    CSB_Write(regs, reg_bias+4 , DAT_IN_HEAD_STRIDE         )
    CSB_Write(regs, reg_bias+5 , DAT_IN_LINE_STRIDE         )
    CSB_Write(regs, reg_bias+6 , feature_out_base           )
    CSB_Write(regs, reg_bias+7 , DAT_OUT_HEAD_STRIDE        )
    CSB_Write(regs, reg_bias+8 , DAT_OUT_LINE_STRIDE        )
    CSB_Write(regs, reg_bias+9 , (Token+Tout-1)//Tout       )
    CSB_Write(regs, reg_bias+10, Feature_Head               )
    CSB_Write(regs, reg_bias+11, Token                      )
    CSB_Write(regs, reg_bias+12, Dynamic_Token              )
    CSB_Write(regs, reg_bias+13, Token - last_token         )
    CSB_Write(regs, reg_bias+14, last_token                 )
    CSB_Write(regs, reg_bias+15, 0                          )
    CSB_Write(regs, reg_bias+16, onchip                     )
    CSB_Write(regs, reg_bias+17, 0b00_1000                  )
    CSB_Read(regs, reg_bias+1,1)
    return regs


@Tasks.Register("accel.hbm.act", device.HBM0603)
def ACT(**kwargs):
    device = kwargs["device"]
    Token = kwargs["Height"]
    kvcache = kwargs.get("kvcache", 0)
    Head = kwargs.get("Head", 1)
    Width_in = kwargs["Width_in"]
    DAT_OUT_LINE_STRIDE = kwargs.get("DAT_OUT_LINE_STRIDE")
    DAT_OUT_SURFACE_STRIDE = kwargs.get("DAT_OUT_SURFACE_STRIDE")
    DAT_IN_BASE_ADDR = kwargs.get("DAT_IN_BASE_ADDR")
    WT_BASE_ADDR = kwargs.get("WT_BASE_ADDR")
    DAT_OUT_BASE_ADDR = kwargs.get("DAT_OUT_BASE_ADDR")
    last_token = kwargs.get("last_token", 0)

    Tin = device.base_Tin
    Tout = device.Tout
    Pixel_Data_Bytes = device.Pixel_Data_Bytes
    WT_DW = device.MAX_WT_DW
    HBM_AXI_DATA_WIDTH = device.HBM_AXI_DATA_WIDTH
    WT_CH_Tgroup = device.WT_CH_Tgroup
    MAX_WT_DW = device.MAX_WT_DW
    MAX_BN_DW = device.MAX_BN_DW
    HBM_Port = device.HBM_Port
    WT_BRAM_DEPTH = device.WT_BRAM_DEPTH
    AXI_BN_WIDTH = device.AXI_BN_WIDTH
    BN_FIFO_DEP = device.BN_FIFO_DEP
    BN_FIFO_NUM = device.BN_FIFO_NUM
    AXI_DAT_WIDTH = device.AXI_DAT_WIDTH
    log2_AXI_BURST_LEN = device.log2_AXI_BURST_LEN

    Win = If(kvcache, 1, Token - last_token)
    Hin = Head
    CHin = Width_in
    CHout = CHin
    Wout = kwargs.get("Wout", Win)
    Hout = Hin
    CHout_div_Tout = ((CHout + Tout - 1) // Tout)
    CHin_div_Tout = ((CHin + Tout - 1) // Tout)
    CHin_Padding_with_Tout = CHin_div_Tout * Tout
    LN_num_per_AXI_DW = AXI_DAT_WIDTH // (2*MAX_BN_DW)
    Tin_div_Tout = (Tin + Tout - 1) // Tout
    CHout_Padding = CHout_div_Tout * Tout
    DAT_IN_LINE_STRIDE = Pixel_Data_Bytes * Win
    DAT_IN_SURFACE_STRIDE = Pixel_Data_Bytes * Win * Hin
    if DAT_OUT_LINE_STRIDE is None or DAT_OUT_SURFACE_STRIDE is None:
        DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes * Wout
        DAT_OUT_SURFACE_STRIDE = Pixel_Data_Bytes * Wout * Hout

    ## Hardware Testbench
    CHin = CHin_Padding_with_Tout
    parameters_base = WT_BASE_ADDR
    pixel_in = Win
    act_reg_bias = 192
    
    regs = []
    CSB_Write(regs, act_reg_bias+2 , parameters_base         )
    CSB_Write(regs, act_reg_bias+3 , DAT_IN_BASE_ADDR        )
    CSB_Write(regs, act_reg_bias+4 , DAT_IN_SURFACE_STRIDE   )
    CSB_Write(regs, act_reg_bias+5 , DAT_IN_LINE_STRIDE      )
    CSB_Write(regs, act_reg_bias+6 , DAT_OUT_BASE_ADDR       )
    CSB_Write(regs, act_reg_bias+7 , DAT_OUT_SURFACE_STRIDE  )
    CSB_Write(regs, act_reg_bias+8 , DAT_OUT_LINE_STRIDE     )
    CSB_Write(regs, act_reg_bias+9 , CHout_div_Tout          )
    CSB_Write(regs, act_reg_bias+10, Hin                     )
    CSB_Write(regs, act_reg_bias+11, Win                     )
    CSB_Write(regs, act_reg_bias+12, pixel_in                )
    CSB_Write(regs, act_reg_bias+13, CHout_div_Tout          )
    CSB_Write(regs, act_reg_bias+14, CHout                   )
    CSB_Write(regs, act_reg_bias+15, 0                       )
    CSB_Write(regs, act_reg_bias+16, 0                       )
    CSB_Write(regs, act_reg_bias+17,                0b01_0000)
    CSB_Read(regs, act_reg_bias+1, 1)
    return regs


@Tasks.Register("accel.hbm.act", device.EdgeLLMv2)
def ACT_v2(**kwargs):
    device = kwargs["device"]
    Token = kwargs["Height"]
    kvcache = kwargs.get("kvcache", 0)
    Head = kwargs.get("Head", 1)
    Width_in = kwargs["Width_in"]
    DAT_OUT_LINE_STRIDE = kwargs.get("DAT_OUT_LINE_STRIDE")
    DAT_OUT_SURFACE_STRIDE = kwargs.get("DAT_OUT_SURFACE_STRIDE")
    DAT_IN_BASE_ADDR = kwargs.get("DAT_IN_BASE_ADDR")
    WT_BASE_ADDR = kwargs.get("WT_BASE_ADDR")
    DAT_OUT_BASE_ADDR = kwargs.get("DAT_OUT_BASE_ADDR")
    last_token = kwargs.get("last_token", 0)
    DAT_IN_ONCHIP = kwargs.get("DAT_IN_ONCHIP")
    DAT_OUT_ONCHIP = kwargs.get("DAT_OUT_ONCHIP")

    Tin = device.base_Tin
    Tout = device.Tout
    Pixel_Data_Bytes = device.Pixel_Data_Bytes
    WT_DW = device.MAX_WT_DW
    HBM_AXI_DATA_WIDTH = device.HBM_AXI_DATA_WIDTH
    WT_CH_Tgroup = device.WT_CH_Tgroup
    MAX_WT_DW = device.MAX_WT_DW
    MAX_BN_DW = device.MAX_BN_DW
    HBM_Port = device.HBM_Port
    WT_BRAM_DEPTH = device.WT_BRAM_DEPTH
    AXI_BN_WIDTH = device.AXI_BN_WIDTH
    BN_FIFO_DEP = device.BN_FIFO_DEP
    BN_FIFO_NUM = device.BN_FIFO_NUM
    AXI_DAT_WIDTH = device.AXI_DAT_WIDTH
    log2_AXI_BURST_LEN = device.log2_AXI_BURST_LEN

    Win = If(kvcache, 1, Token - last_token)
    Hin = Head
    CHin = Width_in
    CHout = CHin
    Wout = kwargs.get("Wout", Win)
    Hout = Hin
    CHout_div_Tout = ((CHout + Tout - 1) // Tout)
    CHin_div_Tout = ((CHin + Tout - 1) // Tout)
    CHin_Padding_with_Tout = CHin_div_Tout * Tout
    LN_num_per_AXI_DW = AXI_DAT_WIDTH // (2*MAX_BN_DW)
    Tin_div_Tout = (Tin + Tout - 1) // Tout
    CHout_Padding = CHout_div_Tout * Tout
    DAT_IN_LINE_STRIDE = Pixel_Data_Bytes * Win
    DAT_IN_SURFACE_STRIDE = Pixel_Data_Bytes * Win * Hin
    if DAT_OUT_LINE_STRIDE is None or DAT_OUT_SURFACE_STRIDE is None:
        DAT_OUT_LINE_STRIDE = Pixel_Data_Bytes * Wout
        DAT_OUT_SURFACE_STRIDE = Pixel_Data_Bytes * Wout * Hout

    ## Hardware Testbench
    CHin = CHin_Padding_with_Tout
    parameters_base = WT_BASE_ADDR
    pixel_in = Win
    act_reg_bias = 192

    onchip = 0
    if DAT_IN_ONCHIP is not None:
        onchip += 0b1
        DAT_IN_BASE_ADDR = If(kvcache, DAT_IN_ONCHIP, DAT_IN_BASE_ADDR)
    if DAT_OUT_ONCHIP is not None:
        onchip += 0b10
        DAT_OUT_BASE_ADDR = If(kvcache, DAT_OUT_ONCHIP, DAT_OUT_BASE_ADDR)

    if onchip:
        onchip = If(kvcache, onchip, 0)
    
    regs = []
    CSB_Write(regs, act_reg_bias+2 , parameters_base         )
    CSB_Write(regs, act_reg_bias+3 , DAT_IN_BASE_ADDR        )
    CSB_Write(regs, act_reg_bias+4 , DAT_IN_SURFACE_STRIDE   )
    CSB_Write(regs, act_reg_bias+5 , DAT_IN_LINE_STRIDE      )
    CSB_Write(regs, act_reg_bias+6 , DAT_OUT_BASE_ADDR       )
    CSB_Write(regs, act_reg_bias+7 , DAT_OUT_SURFACE_STRIDE  )
    CSB_Write(regs, act_reg_bias+8 , DAT_OUT_LINE_STRIDE     )
    CSB_Write(regs, act_reg_bias+9 , CHout_div_Tout          )
    CSB_Write(regs, act_reg_bias+10, Hin                     )
    CSB_Write(regs, act_reg_bias+11, Win                     )
    CSB_Write(regs, act_reg_bias+12, pixel_in                )
    CSB_Write(regs, act_reg_bias+13, CHout_div_Tout          )
    CSB_Write(regs, act_reg_bias+14, CHout                   )
    CSB_Write(regs, act_reg_bias+15, 0                       )
    CSB_Write(regs, act_reg_bias+16, onchip                  )
    CSB_Write(regs, act_reg_bias+17,                0b01_0000)
    CSB_Read(regs, act_reg_bias+1, 1)
    return regs

