from copy import deepcopy
from dlavm import ne
from . import base as ir


class ReWriter: # TODO

    def __init__(self) -> None:
        self.memo = {}

    def Visit(self, ir_: ir.IR):
        if ir_ in self.memo.keys():
            return self.memo[ir_]
        if isinstance(ir_, ir.Stmt):
            result = self.VisitStmt(ir_)
        elif isinstance(ir_, ir.Expr):
            result = self.VisitExpr(ir_)
        elif isinstance(ir_, ne.Expr):
            result = self.VisitNe(ir_)
        elif isinstance(ir_, (int, float, str)):
            result = self.VisitData(ir_)
        else:
            msg = f"CodeGen not support type of {type(ir_)}, needs driver.ir"
            raise RuntimeError(msg)
        self.memo[ir_] = result
        return result

    def VisitNe(self, expr: ne.Expr):
        return expr

    def VisitData(self, data):
        return data

    def VisitExpr(self, expr: ir.Expr):
        if isinstance(expr, ir.Cast):
            result = self.VisitCast(expr)
        elif isinstance(expr, ir.Var):
            result = self.VisitVar(expr)
        else:
            msg = f"CodeGen not support type of {type(expr)}, needs driver.ir"
            raise RuntimeError(msg)
        return result

    def VisitStmt(self, stmt: ir.Stmt):
        if isinstance(stmt, ir.Function):
            result = self.VisitFunction(stmt)
        elif isinstance(stmt, ir.For):
            result = self.VisitFor(stmt)
        elif isinstance(stmt, ir.If):
            result = self.VisitIf(stmt)
        elif isinstance(stmt, ir.Assign):
            result = self.VisitAssign(stmt)
        elif isinstance(stmt, ir.CSB_Write):
            result = self.VisitCSBWrite(stmt)
        elif isinstance(stmt, ir.CSB_Read):
            result = self.VisitCSBRead(stmt)
        elif isinstance(stmt, ir.MemWriteFile):
            result = self.VisitMemWriteFile(stmt)
        elif isinstance(stmt, ir.MemInit):
            result = self.VisitMemInit(stmt)
        elif isinstance(stmt, ir.StrFormat):
            result = self.VisitStrFormat(stmt)
        elif isinstance(stmt, ir.Inplace):
            result = self.VisitInplace(stmt)
        elif isinstance(stmt, ir.Call):
            result = self.VisitCall(stmt)
        elif isinstance(stmt, ir.Block):
            result = self.VisitBlock(stmt)
        else:
            msg = f"CodeGen not support type of {type(stmt)}, needs driver.ir"
            raise RuntimeError(msg)
        return result

    def VisitFunction(self, stmt: ir.Function):
        new_stmt = deepcopy(stmt)
        new_stmt.body = [self.Visit(b) for b in new_stmt.body]
        return new_stmt

    def VisitBlock(self, stmt: ir.Block):
        new_stmt = deepcopy(stmt)
        new_stmt.body = [self.Visit(b) for b in new_stmt.body]
        return new_stmt

    def VisitCall(self, stmt: ir.Call):
        new_stmt = deepcopy(stmt)
        new_stmt.func = self.Visit(new_stmt.func)
        return new_stmt

    def VisitFor(self, stmt: ir.For):
        new_stmt = deepcopy(stmt)
        new_stmt.init = self.Visit(new_stmt.init)
        new_stmt.extent = self.Visit(new_stmt.extent)
        new_stmt.stride = self.Visit(new_stmt.stride)
        new_stmt.body = [self.Visit(b) for b in stmt.body]
        return new_stmt

    def VisitIf(self, stmt: ir.If):
        new_stmt = deepcopy(stmt)
        new_stmt.then_block = deepcopy(stmt.then_block)
        new_stmt.else_block = deepcopy(stmt.else_block)
        new_stmt.judge = self.Visit(new_stmt.judge)
        new_stmt.then_block.body = [self.Visit(b) for b in stmt.then_block.body]
        new_stmt.else_block.body = [self.Visit(b) for b in stmt.else_block.body]
        return new_stmt

    def VisitAssign(self, stmt: ir.Assign):
        new_stmt = deepcopy(stmt)
        new_stmt.value = self.Visit(stmt.value)
        return new_stmt

    def VisitCSBWrite(self, stmt: ir.CSB_Write):
        new_stmt = deepcopy(stmt)
        new_stmt.addr = self.Visit(stmt.addr)
        new_stmt.data = self.Visit(stmt.data)
        return new_stmt

    def VisitCSBRead(self, stmt: ir.CSB_Read):
        new_stmt = deepcopy(stmt)
        new_stmt.addr = self.Visit(stmt.addr)
        new_stmt.data = self.Visit(stmt.data)
        return new_stmt

    def VisitMemWriteFile(self, stmt: ir.MemWriteFile):
        new_stmt = deepcopy(stmt)
        new_stmt.addr = self.Visit(stmt.addr)
        new_stmt.file = self.Visit(stmt.file)
        new_stmt.size = self.Visit(stmt.size)
        return new_stmt

    def VisitMemInit(self, stmt: ir.MemInit):
        new_stmt = deepcopy(stmt)
        new_stmt.addr = self.Visit(stmt.addr)
        new_stmt.size = self.Visit(stmt.size)
        return new_stmt

    def VisitStrFormat(self, stmt: ir.StrFormat):
        new_stmt = deepcopy(stmt)
        new_stmt.target = self.Visit(stmt.target)
        new_stmt.args = [self.Visit(arg) for arg in stmt.args]
        return new_stmt

    def VisitInplace(self, stmt: ir.Inplace):
        new_stmt = deepcopy(stmt)
        new_stmt.data = self.Visit(stmt.data)
        return new_stmt

    def VisitCast(self, expr: ir.Cast):
        new_expr = deepcopy(expr)
        new_expr.var = self.Visit(expr.var)
        return new_expr

    def VisitVar(self, expr: ir.Var):
        new_expr = deepcopy(expr)
        new_expr.var = self.Visit(expr.var)
        return new_expr

