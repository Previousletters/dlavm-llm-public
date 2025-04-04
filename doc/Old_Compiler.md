# DeepLearning Accelerator Virtual Machine

本项目参照TVM，重新设计编译流程，以加速器为主要的后端Target进行构建，最终预想为以ONNX模型为输入（或其他级别IR）以不同深度学习加速器为输出（Target）的端到端深度学习编译系统。

项目文件结构：

    adr: 编译器基础的数据结构的定义以及算子注册相关，包含算子schedule和make函数
    
    backend: 编译器的后端部分，主要包括算子编译以及代码生成部分，对应多种编译逻辑和生成方法
    
    clib: 在常规处理中需要用到的C语言库的接口，也包含部分数据处理函数
    
    device: 添加不同后端加速器的配置设定，能够进行device的选择
    
    driver: 算子compute相关，即tasks函数，算子对应的寄存器配置生成
    
    frontend: 编译器的前端部分，当前只包含对ONNX框架的支持，后续添加对其他框架或IR（如TVM）的支持
    
    ne: Number Expression数字表达式，为编译器提供动态控制的核心
    
    transform: 编译阶段计算图转换部分，通常涉及计算图形状推理、优化或者权重离线处理等

当前支持的加速器情况如下：

| Accel Name | Config Regs | Offline-Weight | End to End |
|:----------:|:-----------:|:--------------:|:----------:|
|    HBM     |   &#10004;  |    &#10006;    |  &#10006;  |
|   Sparse   |   &#10004;  |    &#10006;    |  &#10006;  |

## 编译器架构

编译器的设计主要以加速器为视角，考虑尽可能为加速器算子服务。但是由于某些情况下，模型算子到加速器算子的映射过程太过陡峭，导致计算图过于冗余，并且代码中出现了很多显而易见的妥协，需要后续用新的方案处理。

#### Expr IR设计

编译器表达式IR的设计参考TVM，以Var，Constant，Call，Tuple，Tensor等作为基础，每个节点根据其数据类型添加了checked_type属性，以便检查和上下节点的连接。

与TVM具有明显不同的是，DLAVM添加了VM的IR类型。VM与Call类似，均为算子执行相关的IR类型，但是与之不同的是，VM算子通常不做任何实际上的处理，而是在编译阶段为了合理的连接上下节点而不出现莫名其妙的转换。所以起名为虚假的。其最大的特点是不会生成任何指令，且不开辟任何运行空间。

在此编译器中，暂未添加Function节点，并且为了轻便，使得所有类型的节点均具有str的函数属性进行print输出。Function节点的添加或许有助于后续划分计算图结构，需要进一步评估尝试。

#### 算子相关

+ Schedule + Compute

DLAVM的算子方案依然是按照Schedule和Compute分离的方案进行，在adr文件夹下的Op对象进行算子注册工作，每个算子除了具有自己的名称外，还具有自己对应的属性函数，会根据相应的任务进行调用。

Schedule主要为了将本层的数据尺寸进行确定和往下传递，即与InferType强相关；Compute在编译器中实际上为对应加速器算子的指令生成任务，也即硬件中testbench以及对应的tasks。

当前的算子注册相较来说偏麻烦，或许需要新的脚本，根据后续需要进行更新

#### 设备与驱动

此处主要是添加对应加速器的驱动以及对应不同的加速器的配置，以进行数据或者malloc相关数据大小的计算等任务。但实际上，device信息会被存储进Tensor中，看起来似乎并非特别恰当。

#### 编译前端

编译前端实际上为编译器的第一个算子映射阶段，根据ONNX框架前端情况，ONNX算子会直接映射为对应编译器中注册的硬件算子情况。然而，模型算子对加速器算子的映射通常都是复杂的，对于某些难以映射的算子，我们只能考虑从ONNX框架入手进行Custom的定义。

除此之外，一些特殊的算子优化实际上需要结合上下算子的合并，才能进行特殊算子的消除，如ONNX.Expand等。所以不可避免地，此类中间算子需要存在，甚至必要的时候需要转化为某些CPU编译器对应的IR。但此类问题并非此编译器的主要任务。

由于DLAVM更适合处理加速器相关的模型，所以整体算子的加载和卸载显得十分有必要。原计划DLAVM将作为TVM的子模块进行，对TVM的IR进行加载，尽可能转化完毕后将非加速器的算子进行卸载，卸载的算子返还给TVM进行处理，即联合TVM进行编译。

#### 编译后端

编译后端提供了一些可供选择的编译对象，如从Testbench中直接取得结果或者从手写驱动中运行获得；除此之外，CodeGen也是此模块的重要组成部分。如何生成对应的高效的可控且好用的C语言runtime代码，需要继续开发。

除此之外，此模块的目标为将IR转换为一个较为完整的新模型而非.h文件，参照华为的atc将ONNX转换为om模型。

#### 计算图转换

此部分设计内容最多，也是算子映射的第二个阶段，结合上下算子进行优化或者转换。

+ Fused Ops

    实际上由于加速器算子的设定，其算子融合与模型中的算子融合方案并不完全一致，所以需要新的策略能够更敏捷的对应起来。FusedLib也被运用，但是依然有一定的局限性。后续会考虑Function节点的运用。

## 开发Log

__2024.04.11__

chatglm2的Block测试中，without kvcache指令生成以及memory配置完全正确，参考为0411_1452.h，后续要测试完整28个Block以及最后的ArgMax模式。




