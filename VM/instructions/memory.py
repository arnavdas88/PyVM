from ..debug import reg_names
from ..Registers import Reg32
from ..util import Instruction, to_int, byteorder, SegmentRegs
from ..misc import sign_extend, parity

from functools import partialmethod as P

import logging
logger = logging.getLogger(__name__)

MAXVALS = [None, (1 << 8) - 1, (1 << 16) - 1, None, (1 << 32) - 1]  # MAXVALS[n] is the maximum value of an unsigned n-bit number
SIGNS   = [None, 1 << 8 - 1, 1 << 16 - 1, None, 1 << 32 - 1]  # SIGNS[n] is the maximum absolute value of a signed n-bit number


####################
# MOV
####################
class MOV(Instruction):
    """
    Move data from one location (a) to another (b).

    Flags:
        None affected

    Operation: b <- a
    """

    def __init__(self):
        self.opcodes = {
            **{
                o: P(self.r_imm, _8bit=True)
                for o in range(0xB0, 0xB8)
                },
            **{
                o: P(self.r_imm, _8bit=False)
                for o in range(0xB8, 0xC0)
                },
            0xC6: P(self.rm_imm, _8bit=True),
            0xC7: P(self.rm_imm, _8bit=False),

            0x88: P(self.rm_r, _8bit=True, reverse=False),
            0x89: P(self.rm_r, _8bit=False, reverse=False),

            0x8A: P(self.rm_r, _8bit=True, reverse=True),
            0x8B: P(self.rm_r, _8bit=False, reverse=True),

            0x8C: P(self.sreg_rm, reverse=True),
            0x8E: P(self.sreg_rm, reverse=False),

            0xA0: P(self.r_moffs, reverse=False, _8bit=True),
            0xA1: P(self.r_moffs, reverse=False, _8bit=False),

            0xA2: P(self.r_moffs, reverse=True, _8bit=True),
            0xA3: P(self.r_moffs, reverse=True, _8bit=False),
            }

    def r_imm(vm, _8bit) -> True:
        sz = 1 if _8bit else vm.operand_size

        imm = vm.mem.get_eip(vm.eip, sz)

        vm.eip += sz

        r = vm.opcode & 0b111
        vm.reg.set(r, sz, imm)

        logger.debug('mov %s, %x', reg_names[r][sz], imm)

        return True

    def rm_imm(vm, _8bit) -> bool:
        sz = 1 if _8bit else vm.operand_size
        old_eip = vm.eip

        RM, R = vm.process_ModRM(sz, sz)

        if R[1] != 0:
            vm.eip = old_eip
            return False  # this is not MOV

        type, loc, _ = RM

        imm = vm.mem.get_eip(vm.eip, sz)
        vm.eip += sz

        (vm.mem if type else vm.reg).set(loc, sz, imm)

        logger.debug('mov %s, 0x%x', hex(loc) if type else reg_names[loc][sz], imm)

        return True

    def rm_r(vm, _8bit, reverse=False) -> True:
        sz = 1 if _8bit else vm.operand_size

        RM, R = vm.process_ModRM(sz)

        type, loc, _ = RM

        if reverse:
            data = (vm.mem if type else vm.reg).get(loc, sz)
            
            vm.reg.set(R[1], sz, data)

            logger.debug('mov %s, %s=0x%x', reg_names[R[1]][sz], hex(loc) if type else reg_names[loc][sz], data)
        else:
            data = vm.reg.get(R[1], R[2])
            
            (vm.mem if type else vm.reg).set(loc, R[2], data)

            logger.debug('mov %s, %s=0x%x', hex(loc) if type else reg_names[loc][sz], reg_names[R[1]][sz], data)

        return True

    def r_moffs(vm, _8bit, reverse=False) -> True:
        sz = 1 if _8bit else vm.operand_size

        loc = vm.mem.get_eip(vm.eip, vm.address_size)
        vm.eip += vm.address_size

        if reverse:
            data = vm.reg.get(0, sz)
            vm.mem.set(loc, sz, data)

            logger.debug('mov moffs 0x%08x, %s=0x%x', loc, reg_names[0][sz], data)
        else:
            data = vm.mem.get(loc, sz)
            vm.reg.set(0, sz, data)

            logger.debug('mov %s, moffs 0x%08x=0x%x', reg_names[0][sz], loc, data)

        return True

    def sreg_rm(vm, reverse) -> True:
        # TODO: implement MOV with sreg
        sz = 2

        RM, R = vm.process_ModRM(sz)

        type, From, size = RM

        SRC = (vm.mem if type else vm.reg).get(From, size)

        index, TI, RPL = SRC >> 3, (SRC >> 2) & 1, SRC & 0b11

        if not reverse:
            logger.debug(f'About to move to sreg({SegmentRegs(R[1]).name}) from index={index}, table={["GDT", "LDT"][TI]}, privilege={RPL}')

            if TI == 0:  # move from GDT
                descr = vm.GDT[index]
            else:  # move from LDT
                raise RuntimeError('LDT not implemented')

            vm.sreg.set(R[1], SRC, descr)
            # vm.reg.sreg[R[1]].from_bytes(SRC, descr)
        else:
            raise RuntimeError('mov r/m, sreg is not supported yet')

        return True


####################
# MOVSX / MOVSXD / MOVZX
####################
class MOVSX(Instruction):
    """
    Move and sign extend
    """

    def __init__(self):
        self.opcodes = {
            0x0FBE: P(self.r_rm, _8bit=True, movsxd=False),
            0x0FBF: P(self.r_rm, _8bit=False, movsxd=False),

            0x63: P(self.r_rm, _8bit=False, movsxd=True),

            0x0FB6: P(self.r_rm_movzx, _8bit=True),
            0x0FB7: P(self.r_rm_movzx, _8bit=False),
        }

    def r_rm_movzx(vm, _8bit) -> True:
        sz = 1 if _8bit else 2

        RM, R = vm.process_ModRM(sz, vm.operand_size)

        type, loc, size = RM

        #print(f'memory.MOVSX.r_rm_movzx reg(loc={R[1]}), {"mem" if type else "reg"}(loc=0x{loc:08x}, size={size})')
        SRC = (vm.mem if type else vm.reg).get(loc, size)  # auto zero extension

        vm.reg.set(R[1], R[2], SRC)

        logger.debug('movzx %s, %s=0x%x', reg_names[R[1]][4], hex(loc) if type else reg_names[loc][size], SRC)

        return True

    def r_rm(vm, _8bit, movsxd: bool) -> True:
        if not movsxd:
            RM, R = vm.process_ModRM(1 if _8bit else 2, vm.operand_size)  # r/m8 or r/m16
        else:
            RM, R = vm.process_ModRM(vm.operand_size)  # same sizes!

        type, From, size = RM

        SRC = (vm.mem if type else vm.reg).get(From, size, True)

        # print(f'Sign-extend {size} bytes to fit {R[2]} bytes ({SRC.hex()} -> {SRC_.hex()})')

        vm.reg.set(R[1], R[2], SRC)

        logger.debug('movsx%s %s, %s=0x%x', 'd' if movsxd else '', reg_names[R[1]][R[2]], hex(From) if type else reg_names[From][size], SRC)

        return True
    

####################
# PUSH
####################
class PUSH(Instruction):
    """
    Push data onto the stack.
    """

    def __init__(self):
        self.opcodes = {
            **{
                o: self.r
                for o in range(0x50, 0x58)
                },
            0xFF  : self.rm,

            0x6A  : P(self.imm, _8bit=True),
            0x68  : P(self.imm, _8bit=False),

            0x0E  : P(self.sreg, 'CS'),
            0x16  : P(self.sreg, 'SS'),
            0x1E  : P(self.sreg, 'DS'),
            0x06  : P(self.sreg, 'ES'),

            0x0FA0: P(self.sreg, 'FS'),
            0x0FA8: P(self.sreg, 'GS')
            }

    def r(vm) -> True:
        sz = vm.operand_size

        loc = vm.opcode & 0b111
        data = vm.reg.get(loc, sz)

        vm.stack_push(data)

        logger.debug('push %s=0x%x', reg_names[loc][sz], data)

        return True

    def rm(vm) -> bool:
        old_eip = vm.eip
        sz = vm.operand_size

        RM, R = vm.process_ModRM(sz)

        if R[1] != 6:
            vm.eip = old_eip
            return False  # this is not PUSH rm

        type, loc, _ = RM

        data = (vm.mem if type else vm.reg).get(loc, sz)
        vm.stack_push(data)

        logger.debug('push %s=0x%x', hex(loc) if type else reg_names[loc][sz], data)

        return True

    def imm(vm, _8bit=False) -> True:
        sz = 1 if _8bit else vm.operand_size

        data = vm.mem.get_eip(vm.eip, sz, sz < vm.operand_size)
        vm.eip += sz

        vm.stack_push(data)

        logger.debug('push 0x%x', data)

        return True

    def sreg(vm, reg: str) -> True:
        """
        Push a segment register onto the stack.

        :param reg: the name of the register to be pushed.
        """
        data = getattr(vm.reg, reg).to_bytes(2, byteorder)

        if len(data) < vm.operand_size:
            data = zero_extend(data, vm.operand_size)

        vm.stack_push(data)

        logger.debug('push %s', reg)

        return True


####################
# PUSHF / PUSHFD
####################
class PUSHF(Instruction):
    """
    Decrements the stack pointer by 4 (if the current operand-size attribute is 32) and pushes the entire contents of
    the EFLAGS register onto the stack, or decrements the stack pointer by 2 (if the operand-size attribute is 16) and
    pushes the lower 16 bits of the EFLAGS register (that is, the FLAGS register) onto the stack.
    These instructions reverse the operation of the POPF/POPFD instructions.
    """
    def __init__(self):
        self.opcodes = {
            0x9C: self.pushf
        }

    def pushf(vm) -> True:
        # TODO: this should check for some kind of mode or whatnot
        tmpEFLAGS = vm.reg.eflags & (0x00FCFFFF if vm.operand_size == 4 else 0x0000FFFF)

        vm.stack_push(tmpEFLAGS.to_bytes(vm.stack_address_size, byteorder))

        logger.debug('pushf%s', 'd' if vm.operand_size == 4 else '')

        return True


####################
# PUSHA / PUSHAD
####################
class PUSHA(Instruction):
    def __init__(self):
        self.opcodes = {
            0x60: self.pusha
        }

    def pusha(vm) -> True:
        regs_to_push_1 = 0, 1, 2, 3
        regs_to_push_2 = 5, 6, 7

        Temp = vm.reg.get(4, vm.operand_size)

        for reg in regs_to_push_1:
            vm.stack_push(vm.reg.get(reg, vm.operand_size))

        vm.stack_push(Temp)

        for reg in regs_to_push_2:
            vm.stack_push(vm.reg.get(reg, vm.operand_size))

        logger.debug('pusha%s', 'd' if vm.operand_size == 4 else '')

        return True


####################
# POPA / POPAD
####################
class POPA(Instruction):
    def __init__(self):
        self.opcodes = {
            0x61: self.popa
        }

    def popa(vm) -> True:
        regs_to_pop_1 = 7, 6, 5
        regs_to_pop_2 = 3, 2, 1, 0

        for reg in regs_to_pop_1:
            vm.reg.set(reg, vm.stack_pop(vm.operand_size))

        esp = to_int(vm.reg.get(4, vm.operand_size))
        vm.reg.set(4, (esp + vm.operand_size).to_bytes(4, byteorder))

        for reg in regs_to_pop_2:
            vm.reg.set(reg, vm.stack_pop(vm.operand_size))

        logger.debug('popa%s', 'd' if vm.operand_size == 4 else '')

        return True


####################
# POPF / POPFD
####################
class POPF(Instruction):
    """
    Pops a doubleword (POPFD) from the top of the stack (if the current operand-size attribute is 32) and stores
    the value in the EFLAGS register, or pops a word from the top of the stack (if the operand-size attribute is 16) and
    stores it in the lower 16 bits of the EFLAGS register (that is, the FLAGS register). These instructions reverse
    the operation of the PUSHF/PUSHFD/PUSHFQ instructions.
    """
    def __init__(self):
        self.opcodes = {
            0x9D: self.popf
        }

    def popf(vm) -> True:
        # TODO: this should check for some kind of mode or whatnot
        tmpEFLAGS = to_int(vm.stack_pop(vm.stack_address_size))
        vm.reg.eflags = tmpEFLAGS & MAXVALS[vm.operand_size]

        logger.debug('popf%s', 'd' if vm.operand_size == 4 else '')

        return True


####################
# POP
####################
class POP(Instruction):
    """
    Pop data from the stack.
    """

    def __init__(self):
        self.opcodes = {
            **{
                o: self.r
                for o in range(0x58, 0x60)
                },
            0x8F  : self.rm,

            0x1F  : P(self.sreg, 'DS'),
            0x07  : P(self.sreg, 'ES'),
            0x17  : P(self.sreg, 'SS'),

            0x0FA1: P(self.sreg, 'FS', _32bit=True),
            0x0FA9: P(self.sreg, 'GS', _32bit=True)
            }

    def r(vm) -> True:
        sz = vm.operand_size

        loc = vm.opcode & 0b111
        data = vm.stack_pop(sz)
        vm.reg.set(loc, sz, data)

        logger.debug('pop %s := %x', reg_names[loc][sz], data)
        # if debug: print(f'pop {reg_names[loc][sz]} <- {bytes(data)}')

        return True

    def rm(vm) -> bool:
        sz = vm.operand_size
        old_eip = vm.eip

        RM, R = vm.process_ModRM(sz, sz)

        if R[1] != 0:
            vm.eip = old_eip
            return False  # this is not POP rm

        type, loc, _ = RM

        data = vm.stack_pop(sz)

        (vm.mem if type else vm.reg).set(loc, sz, data)

        logger.debug('pop %s := 0x%x', hex(loc) if type else reg_names[loc][sz], data)

        return True

    def sreg(vm, reg: str, _32bit=False) -> True:
        sz = 4 if _32bit else 2

        data = vm.stack_pop(sz)

        setattr(vm.reg, reg, to_int(data, False))

        logger.debug('pop %s := %s', reg, data.hex())

        return True


####################
# LEA
####################
class LEA(Instruction):
    def __init__(self):
        self.opcodes = {
            0x8D: self.r_rm
            }

    def r_rm(vm) -> True:
        RM, R = vm.process_ModRM(vm.address_size, vm.operand_size)  # should be address_size

        type, loc, sz = RM

        if (vm.operand_size == 2) and (vm.address_size == 2):
            tmp = loc
        elif (vm.operand_size == 2) and (vm.address_size == 4):
            tmp = loc & 0xffff
        elif (vm.operand_size == 4) and (vm.address_size == 2):
            tmp = loc & 0xffff
        elif (vm.operand_size == 4) and (vm.address_size == 4):
            tmp = loc
        else:
            raise RuntimeError("Invalid operand size / address size")

        tmp &= MAXVALS[vm.address_size]

        data = tmp
        vm.reg.set(R[1], vm.operand_size, data)

        logger.debug('lea %s, %s == 0x%08x', reg_names[R[1]][sz], hex(loc) if type else reg_names[loc][sz], data)

        return True


####################
# XCHG
####################
class XCHG(Instruction):
    def __init__(self):
        self.opcodes = {
            **{
                o: self.eax_r
                for o in range(0x90, 0x98)
                },
            0x86: P(self.rm_r, _8bit=True),
            0x87: P(self.rm_r, _8bit=False)
            }

    def eax_r(vm) -> True:
        sz = vm.operand_size
        loc = vm.opcode & 0b111

        if loc != 0:  # not EAX
            eax_val = vm.reg.get(0, sz)
            other_val = vm.reg.get(loc, sz)
            vm.reg.set(0, sz, other_val)
            vm.reg.set(loc, sz, eax_val)

            logger.debug('xchg eax=0x%x, %s=0x%x', eax_val, reg_names[loc][sz], other_val)
        else:
            logger.debug('xchg eax, eax')

        return True

    def rm_r(vm, _8bit) -> True:
        sz = 1 if _8bit else vm.operand_size

        RM, R = vm.process_ModRM(sz, sz)
        type, loc, _ = RM

        if loc != R[1]:
            a_val = (vm.mem if type else vm.reg).get(loc, sz)
            b_val = vm.reg.get(R[1], sz)
            (vm.mem if type else vm.reg).set(loc, b_val)
            vm.reg.set(R[1], a_val)

            logger.debug('xchg %s=%s, %s=%s', hex(loc) if type else reg_names[loc][sz], a_val, reg_names[loc][sz], b_val)
        else:
            logger.debug('xchg %s, %s', hex(loc) if type else reg_names[loc][sz], hex(loc) if type else reg_names[loc][sz])

        return True


####################
# CMPXCHG
####################
class CMPXCHG(Instruction):
    def __init__(self):
        self.opcodes = {
            0x0FB0: P(self.rm_r, _8bit=True),
            0x0FB1: P(self.rm_r, _8bit=False)
        }

    def rm_r(vm, _8bit) -> True:
        sz = 1 if _8bit else vm.operand_size

        RM, R = vm.process_ModRM(sz)

        type, loc, _ = RM

        a = vm.reg.get(0, sz)  # AL/AX/EAX
        b = (vm.mem if type else vm.reg).get(loc, sz)

        # BEGIN compare a and b
        c = a + MAXVALS[sz] + 1 - b

        sign_a = (a >> (sz * 8 - 1)) & 1
        sign_b = (b >> (sz * 8 - 1)) & 1
        sign_c = (c >> (sz * 8 - 1)) & 1

        vm.reg.eflags.OF = (sign_a != sign_b) and (sign_a != sign_c)
        vm.reg.eflags.CF = b > a
        vm.reg.eflags.AF = (b & 255) > (a & 255)
        vm.reg.eflags.SF = sign_c

        c &= MAXVALS[sz]

        vm.reg.eflags.ZF = c == 0
        vm.reg.eflags.PF, parity(c)
        # END compare a and b

        accumulator, temp = a, b

        if vm.reg.eflags.ZF:
            (vm.mem if type else vm.reg).set(loc, sz, vm.reg.get(R[1], sz))
        else:
            #_temp = temp.to_bytes(sz, byteorder)
            vm.reg.set(0, sz, temp)
            (vm.mem if type else vm.reg).set(loc, sz, temp)

        return True


####################
# CBW / CWDE
####################
class CBW(Instruction):
    def __init__(self):
        self.opcodes = {
            0x98: self.cbwcwde
            }

    def cbwcwde(vm) -> True:
        vm.reg.set(0, sign_extend(vm.reg.get(0, vm.operand_size // 2), vm.operand_size))

        logger.debug('cbw' if vm.operand_size == 2 else 'cwde')
        return True


####################
# CMC
####################
class CMC(Instruction):
    def __init__(self):
        self.opcodes = {
            0xF5: self.cmc
            }

    def cmc(vm) -> True:
        vm.reg.eflags_set(Reg32.CF, not vm.reg.eflags_get(Reg32.CF))

        logger.debug('cmc')

        return True


####################
# MOVS
####################
class MOVS(Instruction):
    def __init__(self):
        self.opcodes = {
            0xA4: P(self.movs, _8bit=True),
            0xA5: P(self.movs, _8bit=False)
            }

    def movs(vm, _8bit: bool) -> True:
        sz = 1 if _8bit else vm.operand_size

        esi = vm.reg.get(6, vm.address_size)
        edi = vm.reg.get(7, vm.address_size)

        esi_init = esi

        old_override = vm.mem.segment_override
        vm.mem.segment_override = SegmentRegs.DS
        esi_mem = vm.mem.get(esi, sz)

        vm.mem.segment_override = SegmentRegs.ES
        vm.mem.set(edi, sz, esi_mem)

        vm.mem.segment_override = old_override

        if not vm.reg.eflags.DF:
            esi += sz
            edi += sz
        else:
            esi -= sz
            edi -= sz

        esi &= MAXVALS[vm.address_size]
        edi &= MAXVALS[vm.address_size]

        vm.reg.set(6, vm.address_size, esi)
        vm.reg.set(7, vm.address_size, edi)

        logger.debug('movs%s [edi]:=0x%x, [esi=0x%x]', 'b' if sz == 1 else ('w' if sz == 2 else 'd'), esi_mem, esi_init)

        return True


####################
# CWD / CDQ
####################
class CWD(Instruction):
    def __init__(self):
        self.opcodes = {
            0x99: self.cwd_cdq
            }

    def cwd_cdq(vm) -> True:
        sz = vm.operand_size

        tmp = vm.reg.get(0, sz, True)  # AX / EAX

        vm.reg.set(2, sz, tmp >> (sz * 8))  # DX/EDX
        vm.reg.set(0, sz, tmp & MAXVALS[sz])  # AX/EAX

        logger.debug('cwd' if sz == 2 else 'cdq')

        return True


####################
# CLC / CLD / STC / STD
####################
class CLC(Instruction):
    def __init__(self):
        self.opcodes = {
            0xF8: P(self.set_stuff, 'CF', 0),
            0xFC: P(self.set_stuff, 'DF', 0),
            0xF9: P(self.set_stuff, 'CF', 1),
            0xFD: P(self.set_stuff, 'DF', 1),
            }

    def set_stuff(vm, flag: str, val: int) -> True:
        setattr(vm.reg.eflags, flag, val)

        logger.debug('%s%s', ('cl', 'st')[val], flag[0])

        return True


####################
# BSF / BSR
####################
class BitScan(Instruction):
    """
    Searches the source operand (second operand) for the least significant set bit (1 bit).
    If a least significant 1 bit is found, its bit index is stored in the destination operand (first operand).
    The bit index is an unsigned offset from bit 0 of the source operand.
    If the content of the source operand is 0, the content of the destination operand is undefined.
    """

    def __init__(self):
        self.opcodes = {
            0x0FBC: self.bsf
        }

    def bsf(vm) -> True:
        sz = vm.operand_size

        RM, R = vm.process_ModRM(sz)

        type, loc, _ = RM

        SRC = (vm.mem if type else vm.reg).get(loc, sz)
        SRC_orig = SRC

        if SRC == 0:
            vm.reg.eflags.ZF = 1

            logger.debug(
                'bsf %s, %s=%032b',
                hex(loc) if type else reg_names[loc][sz], reg_names[loc][sz], SRC_orig
            )
            return True

        vm.reg.eflags.ZF = 0

        temp = 0
        while SRC & 1 == 0:
            temp += 1
            SRC >>= 1

        vm.reg.set(R[1], sz, temp)

        logger.debug(
            'bsf %s, %s=%032b',
            hex(loc) if type else reg_names[loc][sz], reg_names[loc][sz], SRC_orig
        )

        return True