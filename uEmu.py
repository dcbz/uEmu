#
#  uEmu.py
#  Micro Emulator
#
#  Created by Alexander Hude on 26/07/17.
#  Copyright (c) 2017 Alexander Hude. All rights reserved.
#

UEMU_USE_AS_SCRIPT      = True    # Set to `False` if you want to load uEmu automatically as IDA Plugin

UEMU_PLUGIN_NAME        = "uEmu"
UEMU_PLUGIN_MENU_NAME   = "uEmu"

# === Import

import pickle
import threading
import json
import os
import collections
import struct

# IDA Python SDK
from idaapi import *
from idc import *
from idautils import *

if IDA_SDK_VERSION >= 700:
    # functions
    IDAAPI_ScreenEA     = get_screen_ea
    IDAAPI_IsCode       = is_code
    IDAAPI_DelItems     = del_items
    IDAAPI_MakeCode     = create_insn
    IDAAPI_GetFlags     = get_full_flags
    IDAAPI_SetColor     = set_color
    IDAAPI_IsLoaded     = is_loaded
    IDAAPI_HasValue     = has_value
    IDAAPI_GetBptQty    = get_bpt_qty
    IDAAPI_GetBptEA     = get_bpt_ea
    IDAAPI_GetBptAttr   = get_bpt_attr
    IDAAPI_SegStart     = get_segm_start
    IDAAPI_SegEnd       = get_segm_end
    IDAAPI_GetBytes     = get_bytes
    IDAAPI_AskYN        = ask_yn
    IDAAPI_AskFile      = ask_file
    IDAAPI_AskLong      = ask_long
    IDAAPI_NextHead     = next_head
    IDAAPI_GetDisasm    = generate_disasm_line
    IDAAPI_NextThat     = next_that
    IDAAPI_Jump         = jumpto
    # classes
    IDAAPI_Choose       = Choose
else:
    # functions
    IDAAPI_ScreenEA     = ScreenEA
    IDAAPI_IsCode       = isCode
    IDAAPI_DelItems     = MakeUnkn
    IDAAPI_MakeCode     = MakeCode
    IDAAPI_GetFlags     = getFlags
    IDAAPI_SetColor     = SetColor
    IDAAPI_IsLoaded     = isLoaded
    IDAAPI_HasValue     = hasValue
    IDAAPI_GetBptQty    = GetBptQty
    IDAAPI_GetBptEA     = GetBptEA
    IDAAPI_GetBptAttr   = GetBptAttr
    IDAAPI_SegStart     = SegStart
    IDAAPI_SegEnd       = SegEnd
    IDAAPI_GetBytes     = get_many_bytes
    IDAAPI_AskYN        = AskYN
    IDAAPI_AskFile      = AskFile
    IDAAPI_AskLong      = AskLong
    IDAAPI_NextHead     = NextHead
    IDAAPI_GetDisasm    = GetDisasmEx
    IDAAPI_NextThat     = nextthat
    IDAAPI_Jump         = Jump
    # classes
    IDAAPI_Choose       = Choose2

# PyQt
from PyQt5 import QtCore, QtWidgets
from PyQt5.QtWidgets import (QPushButton, QHBoxLayout, QLineEdit,QTextEdit, QVBoxLayout, QComboBox)

# Unicorn SDK
from unicorn import *
from unicorn.arm_const import *
from unicorn.arm64_const import *
from unicorn.mips_const import *
from unicorn.x86_const import *

# === Configuration

class UEMU_CONFIG:
    
    IDAViewColor_PC     = 0x00B3CBFF
    IDAViewColor_Reset  = 0xFFFFFFFF

    UnicornPageSize     = 0x1000

# === Helpers

class UEMU_HELPERS:

    # Menu

    MenuItem = collections.namedtuple("MenuItem", ["action", "handler", "title", "tooltip", "shortcut", "popup"])

    class IdaMenuActionHandler(action_handler_t):
        def __init__(self, handler, action):
            action_handler_t.__init__(self)
            self.action_handler = handler
            self.action_type = action
    
        def activate(self, ctx):
            if ctx.form_type == BWN_DISASM:
                self.action_handler.handle_menu_action(self.action_type)
            return 1

        # This action is always available.
        def update(self, ctx):
            return AST_ENABLE_ALWAYS

    # Others

    @staticmethod
    def ALIGN_PAGE_DOWN(x):
        return x & ~(UEMU_CONFIG.UnicornPageSize - 1)
        
    @staticmethod
    def ALIGN_PAGE_UP(x):
        return (x + UEMU_CONFIG.UnicornPageSize - 1) & ~(UEMU_CONFIG.UnicornPageSize-1)
    
    @staticmethod
    def inf_is_be():
        if IDA_SDK_VERSION >= 700:
            return cvar.inf.is_be()
        else:
            return cvar.inf.mf

    @staticmethod
    def get_arch():
        if ph.id == PLFM_386 and ph.flag & PR_USE64:
            return "x64"
        elif ph.id == PLFM_386 and ph.flag & PR_USE32:
            return "x86"
        elif ph.id == PLFM_ARM and ph.flag & PR_USE64:
            if UEMU_HELPERS.inf_is_be():
                return "arm64be"
            else:
                return "arm64le"
        elif ph.id == PLFM_ARM and ph.flag & PR_USE32:
            if UEMU_HELPERS.inf_is_be():
                return "armbe"
            else:
                return "armle"
        elif ph.id == PLFM_MIPS and ph.flag & PR_USE64:
            if UEMU_HELPERS.inf_is_be():
                return "mips64be"
            else:
                return "mips64le"
        elif ph.id == PLFM_MIPS and ph.flag & PR_USE32:
            if UEMU_HELPERS.inf_is_be():
                return "mipsbe"
            else:
                return "mipsle"
        else:
            return ""

    @staticmethod
    def get_register_map(arch):
        if arch.startswith("arm64"):
            arch = "arm64"
        elif arch.startswith("arm"):
            arch = "arm"
        elif arch.startswith("mips"):
            arch = "mips"

        registers = {
            "x64" : [
                [ "rax",    UC_X86_REG_RAX  ],
                [ "rbx",    UC_X86_REG_RBX  ],
                [ "rcx",    UC_X86_REG_RCX  ],
                [ "rdx",    UC_X86_REG_RDX  ],
                [ "rsi",    UC_X86_REG_RSI  ],
                [ "rdi",    UC_X86_REG_RDI  ],
                [ "rbp",    UC_X86_REG_RBP  ],
                [ "rsp",    UC_X86_REG_RSP  ],
                [ "r8",     UC_X86_REG_R8   ],
                [ "r9",     UC_X86_REG_R9   ],
                [ "r10",    UC_X86_REG_R10  ],
                [ "r11",    UC_X86_REG_R11  ],
                [ "r12",    UC_X86_REG_R12  ],
                [ "r13",    UC_X86_REG_R13  ],
                [ "r14",    UC_X86_REG_R14  ],
                [ "r15",    UC_X86_REG_R15  ],
                [ "rip",    UC_X86_REG_RIP  ],
                [ "sp",     UC_X86_REG_SP   ],
            ],
            "x86" : [
                [ "eax",    UC_X86_REG_EAX  ],
                [ "ebx",    UC_X86_REG_EBX  ],
                [ "ecx",    UC_X86_REG_ECX  ],
                [ "edx",    UC_X86_REG_EDX  ],
                [ "esi",    UC_X86_REG_ESI  ],
                [ "edi",    UC_X86_REG_EDI  ],
                [ "ebp",    UC_X86_REG_EBP  ],
                [ "esp",    UC_X86_REG_ESP  ],
                [ "eip",    UC_X86_REG_EIP  ],
                [ "sp",     UC_X86_REG_SP   ],
            ],        
            "arm" : [
                [ "R0",     UC_ARM_REG_R0  ],
                [ "R1",     UC_ARM_REG_R1  ],
                [ "R2",     UC_ARM_REG_R2  ],
                [ "R3",     UC_ARM_REG_R3  ],
                [ "R4",     UC_ARM_REG_R4  ],
                [ "R5",     UC_ARM_REG_R5  ],
                [ "R6",     UC_ARM_REG_R6  ],
                [ "R7",     UC_ARM_REG_R7  ],
                [ "R8",     UC_ARM_REG_R8  ],
                [ "R9",     UC_ARM_REG_R9  ],
                [ "R10",    UC_ARM_REG_R10 ],
                [ "R11",    UC_ARM_REG_R11 ],
                [ "R12",    UC_ARM_REG_R12 ],
                [ "PC",     UC_ARM_REG_PC  ],
                [ "SP",     UC_ARM_REG_SP  ],
                [ "LR",     UC_ARM_REG_LR  ],
                [ "CPSR",   UC_ARM_REG_CPSR ]
            ],
            "arm64" : [
                [ "X0",     UC_ARM64_REG_X0  ],
                [ "X1",     UC_ARM64_REG_X1  ],
                [ "X2",     UC_ARM64_REG_X2  ],
                [ "X3",     UC_ARM64_REG_X3  ],
                [ "X4",     UC_ARM64_REG_X4  ],
                [ "X5",     UC_ARM64_REG_X5  ],
                [ "X6",     UC_ARM64_REG_X6  ],
                [ "X7",     UC_ARM64_REG_X7  ],
                [ "X8",     UC_ARM64_REG_X8  ],
                [ "X9",     UC_ARM64_REG_X9  ],
                [ "X10",    UC_ARM64_REG_X10 ],
                [ "X11",    UC_ARM64_REG_X11 ],
                [ "X12",    UC_ARM64_REG_X12 ],
                [ "X13",    UC_ARM64_REG_X13 ],
                [ "X14",    UC_ARM64_REG_X14 ],
                [ "X15",    UC_ARM64_REG_X15 ],
                [ "X16",    UC_ARM64_REG_X16 ],
                [ "X17",    UC_ARM64_REG_X17 ],
                [ "X18",    UC_ARM64_REG_X18 ],
                [ "X19",    UC_ARM64_REG_X19 ],
                [ "X20",    UC_ARM64_REG_X20 ],
                [ "X21",    UC_ARM64_REG_X21 ],
                [ "X22",    UC_ARM64_REG_X22 ],
                [ "X23",    UC_ARM64_REG_X23 ],
                [ "X24",    UC_ARM64_REG_X24 ],
                [ "X25",    UC_ARM64_REG_X25 ],
                [ "X26",    UC_ARM64_REG_X26 ],
                [ "X27",    UC_ARM64_REG_X27 ],
                [ "X28",    UC_ARM64_REG_X28 ],
                [ "PC",     UC_ARM64_REG_PC  ],
                [ "SP",     UC_ARM64_REG_SP  ],
                [ "FP",     UC_ARM64_REG_FP  ],
                [ "LR",     UC_ARM64_REG_LR  ],
                [ "NZCV",   UC_ARM64_REG_NZCV ]
            ],
            "mips" : [
                [ "zero",   UC_MIPS_REG_0   ],
                [ "at",     UC_MIPS_REG_1   ],
                [ "v0",     UC_MIPS_REG_2   ],
                [ "v1",     UC_MIPS_REG_3   ],
                [ "a0",     UC_MIPS_REG_4   ],
                [ "a1",     UC_MIPS_REG_5   ],
                [ "a2",     UC_MIPS_REG_6   ],
                [ "a3",     UC_MIPS_REG_7   ],
                [ "t0",     UC_MIPS_REG_8   ],
                [ "t1",     UC_MIPS_REG_9   ],
                [ "t2",     UC_MIPS_REG_10  ],
                [ "t3",     UC_MIPS_REG_11  ],
                [ "t4",     UC_MIPS_REG_12  ],
                [ "t5",     UC_MIPS_REG_13  ],
                [ "t6",     UC_MIPS_REG_14  ],
                [ "t7",     UC_MIPS_REG_15  ],
                [ "s0",     UC_MIPS_REG_16  ],
                [ "s1",     UC_MIPS_REG_17  ],
                [ "s2",     UC_MIPS_REG_18  ],
                [ "s3",     UC_MIPS_REG_19  ],
                [ "s4",     UC_MIPS_REG_20  ],
                [ "s5",     UC_MIPS_REG_21  ],
                [ "s6",     UC_MIPS_REG_22  ],
                [ "s7",     UC_MIPS_REG_23  ],
                [ "t8",     UC_MIPS_REG_24  ],
                [ "t9",     UC_MIPS_REG_25  ],
                [ "k0",     UC_MIPS_REG_26  ],
                [ "k1",     UC_MIPS_REG_27  ],
                [ "gp",     UC_MIPS_REG_28  ],
                [ "sp",     UC_MIPS_REG_29  ],
                [ "fp",     UC_MIPS_REG_30  ],
                [ "ra",     UC_MIPS_REG_31  ],
                [ "pc",     UC_MIPS_REG_PC  ],
            ]
        }
        return registers[arch]  

    @staticmethod
    def get_register_bits(arch):
        if arch.startswith("arm64"):
            arch = "arm64"
        elif arch.startswith("arm"):
            arch = "arm"
        elif arch.startswith("mips"):
            arch = "mips"

        registers_bits = {
            "x64"   : 64,
            "x86"   : 32,        
            "arm"   : 32,
            "arm64" : 64,
            "mips"  : 32
        }
        return registers_bits[arch]  

    @staticmethod
    def get_register_ext_map(arch):
        if arch.startswith("arm64"):
            arch = "arm64"
        elif arch.startswith("arm"):
            arch = "arm"
        elif arch.startswith("mips"):
            arch = "mips"

        registers_ext = {
            "x64" : [
            ],
            "x86" : [
            ],        
            "arm" : [
            ],
            "arm64" : [
                [ "Q0",     UC_ARM64_REG_Q0  ],
                [ "Q1",     UC_ARM64_REG_Q1  ],
                [ "Q2",     UC_ARM64_REG_Q2  ],
                [ "Q3",     UC_ARM64_REG_Q3  ],
                [ "Q4",     UC_ARM64_REG_Q4  ],
                [ "Q5",     UC_ARM64_REG_Q5  ],
                [ "Q6",     UC_ARM64_REG_Q6  ],
                [ "Q7",     UC_ARM64_REG_Q7  ],
                [ "Q8",     UC_ARM64_REG_Q8  ],
                [ "Q9",     UC_ARM64_REG_Q9  ],
                [ "Q10",    UC_ARM64_REG_Q10 ],
                [ "Q11",    UC_ARM64_REG_Q11 ],
                [ "Q12",    UC_ARM64_REG_Q12 ],
                [ "Q13",    UC_ARM64_REG_Q13 ],
                [ "Q14",    UC_ARM64_REG_Q14 ],
                [ "Q15",    UC_ARM64_REG_Q15 ],
                [ "Q16",    UC_ARM64_REG_Q16 ],
                [ "Q17",    UC_ARM64_REG_Q17 ],
                [ "Q18",    UC_ARM64_REG_Q18 ],
                [ "Q19",    UC_ARM64_REG_Q19 ],
                [ "Q20",    UC_ARM64_REG_Q20 ],
                [ "Q21",    UC_ARM64_REG_Q21 ],
                [ "Q22",    UC_ARM64_REG_Q22 ],
                [ "Q23",    UC_ARM64_REG_Q23 ],
                [ "Q24",    UC_ARM64_REG_Q24 ],
                [ "Q25",    UC_ARM64_REG_Q25 ],
                [ "Q26",    UC_ARM64_REG_Q26 ],
                [ "Q27",    UC_ARM64_REG_Q27 ],
                [ "Q28",    UC_ARM64_REG_Q28 ],
                [ "Q29",    UC_ARM64_REG_Q29 ],
                [ "Q30",    UC_ARM64_REG_Q30 ],
                [ "Q31",    UC_ARM64_REG_Q31 ],
            ],
            "mips" : [
            ]            
        }
        return registers_ext[arch]  

    @staticmethod
    def get_register_ext_bits(arch):
        if arch.startswith("arm64"):
            arch = "arm64"
        elif arch.startswith("arm"):
            arch = "arm"
        elif arch.startswith("mips"):
            arch = "mips"

        registers_ext_bits = {
            "x64"   : 0,
            "x86"   : 0,        
            "arm"   : 0,
            "arm64" : 128,
            "mips"  : 0
        }
        return registers_ext_bits[arch]  

    @staticmethod
    def is_thumb_ea(ea):
        def handler():
            if ph.id == PLFM_ARM and not ph.flag & PR_USE64:
                if IDA_SDK_VERSION >= 700:
                    t = get_sreg(ea, "T") # get T flag
                else:
                    t = get_segreg(ea, 20) # get T flag
                return t is not BADSEL and t is not 0
            else:
                return False
        return execute_sync(uEmuOnMainCallable(handler), MFF_READ)

    @staticmethod
    def trim_spaces(string):
        return ' '.join(str(string).split())

    @staticmethod
    def is_alt_pressed():
        if QtWidgets.QApplication.keyboardModifiers() & QtCore.Qt.AltModifier:
            return True
        else:
            return False

    class InitedCallable(object):
        def __call__(self, flags):
            return IDAAPI_HasValue(flags)

    class UninitedCallable(object):
        def __call__(self, flags):
            return not IDAAPI_HasValue(flags)

def uemu_log(entry):
    msg("[" + UEMU_PLUGIN_NAME + "]: " + entry + "\n")

# === uEmuInitView

class uEmuInitView(object):
    def __init__(self, owner):
        super(uEmuInitView, self).__init__()
        self.owner = owner

# === uEmuCpuContextView

class uEmuCpuContextView(simplecustviewer_t):

    def __init__(self, owner, extended):
        super(uEmuCpuContextView, self).__init__()
        self.hooks = None
        self.owner = owner
        self.extended = extended
        self.lastAddress = None
        self.lastContext = {}
        self.lastArch = None
        self.columns = None

    def Create(self, title):

        if not simplecustviewer_t.Create(self, title):
            return False

        if IDA_SDK_VERSION >= 700:
            self.menu_cols1 = 1
            self.menu_cols2 = 2
            self.menu_cols3 = 3
            self.menu_update = 4

            class Hooks(UI_Hooks):

                class PopupActionHandler(action_handler_t):
                    def __init__(self, owner, menu_id):
                        action_handler_t.__init__(self)
                        self.owner = owner
                        self.menu_id = menu_id

                    def activate(self, ctx):
                        self.owner.OnPopupMenu(self.menu_id)

                    def update(self, ctx):
                        return AST_ENABLE_ALWAYS

                def __init__(self, form):
                    UI_Hooks.__init__(self)
                    self.form = form

                def finish_populating_widget_popup(self, widget, popup):
                    if self.form.title == get_widget_title(widget):
                        attach_dynamic_action_to_popup(widget, popup, action_desc_t(None, "1 Column",       self.PopupActionHandler(self.form, self.form.menu_cols1),   None, None, -1))
                        attach_dynamic_action_to_popup(widget, popup, action_desc_t(None, "2 Columns",      self.PopupActionHandler(self.form, self.form.menu_cols2),   None, None, -1))
                        attach_dynamic_action_to_popup(widget, popup, action_desc_t(None, "3 Columns",      self.PopupActionHandler(self.form, self.form.menu_cols3),   None, None, -1))
                        attach_action_to_popup(widget, popup, "-", None)
                        attach_dynamic_action_to_popup(widget, popup, action_desc_t(None, "Change Context", self.PopupActionHandler(self.form, self.form.menu_update),  None, None, -1))
                        attach_action_to_popup(widget, popup, "-", None)

            if self.hooks == None:
                self.hooks = Hooks(self)
                self.hooks.hook()            
        else:
            self.menu_cols1 = self.AddPopupMenu("1 Column")
            self.menu_cols2 = self.AddPopupMenu("2 Columns")
            self.menu_cols3 = self.AddPopupMenu("3 Columns")
            self.menu_sep = self.AddPopupMenu("")
            self.menu_update = self.AddPopupMenu("Change Context")
        return True

    def OnPopupMenu(self, menu_id):
        if menu_id == self.menu_cols1:
            self.columns = 1
        elif menu_id == self.menu_cols2:
            self.columns = 2
        elif menu_id == self.menu_cols3:
            self.columns = 3
        elif menu_id == self.menu_update:
            self.owner.change_cpu_context()
        else:
            # Unhandled
            return False
        if self.lastArch is not None and self.lastContext is not None and self.lastAddress is not None:
            self.SetContent(self.lastAddress, None)
        return True

    def SetContent(self, address, context):

        arch = UEMU_HELPERS.get_arch()
        if arch == "":
            return

        self.ClearLines()

        if self.extended:
            hdr_title = COLSTR("  CPU Extended context at [ ", SCOLOR_AUTOCMT)
        else:
            hdr_title = COLSTR("  CPU context at [ ", SCOLOR_AUTOCMT)
        hdr_title += COLSTR("0x%X: " % address, SCOLOR_DREF)
        hdr_title += COLSTR(UEMU_HELPERS.trim_spaces(IDAAPI_GetDisasm(address, 0)), SCOLOR_INSN)
        hdr_title += COLSTR(" ]", SCOLOR_AUTOCMT)
        self.AddLine(hdr_title)
        self.AddLine("")

        if context is None and self.lastContext == 0:
            self.Refresh()
            return

        if self.columns is None:
            cols = self.owner.get_context_columns()
        else:
            cols = self.columns
        if self.extended:
            regList = UEMU_HELPERS.get_register_ext_map(arch)
        else:
            regList = UEMU_HELPERS.get_register_map(arch)
        reg_cnt = len(regList)
        lines = reg_cnt/cols if reg_cnt%cols==0 else (reg_cnt/cols) + 1
        line = ""
        for i in range(lines):
            if i != 0:
                self.AddLine(line)
                line = ""

            for j in xrange(i, reg_cnt, lines):
                reg_label = regList[j][0]
                reg_key = regList[j][1]

                line = line + COLSTR(" %4s: " % str(reg_label), SCOLOR_REG)

                if context is not None:
                    currentValue = context.reg_read(reg_key)
                else:
                    currentValue = self.lastContext[reg_label]

                if ph.flag & PR_USE64:
                    if self.extended:
                        value_format = "0x%.32X"
                    else:
                        value_format = "0x%.16X"
                else:
                    value_format = "0x%.8X"

                if reg_label in self.lastContext:
                    if self.lastContext[reg_label] != currentValue:
                        line += COLSTR(str(value_format % currentValue), SCOLOR_VOIDOP)
                    else:
                        line += COLSTR(str(value_format % currentValue), SCOLOR_NUMBER)
                else:
                    line += COLSTR(str(value_format % currentValue), SCOLOR_NUMBER)

                self.lastContext[reg_label] = currentValue

                line = line.ljust(35 * ((j/lines) + 1))

        self.AddLine(line)

        self.Refresh()
        self.lastArch = arch
        self.lastAddress = address

    def OnClose(self):
        if self.hooks:
            self.hooks.unhook()
            self.hooks = None
        if self.extended:
            self.owner.ext_context_view_closed()
        else:
            self.owner.context_view_closed()

# === uEmuMemoryView

class uEmuMemoryRangeDialog(Form):
    def __init__(self):
        Form.__init__(self, r"""STARTITEM {id:mem_addr}
BUTTON YES* Add
BUTTON CANCEL Cancel
Show Memory Range
Specify start address and size of new memory range.
<##Address\::{mem_addr}> <##Size\::{mem_size}>
<##Comment\::{mem_cmnt}>
""", {
        'mem_addr': Form.NumericInput(swidth=20, tp=Form.FT_HEX),
        'mem_size': Form.NumericInput(swidth=10, tp=Form.FT_DEC),
        'mem_cmnt': Form.StringInput(swidth=41)
        })

class uEmuMemoryView(simplecustviewer_t):
    def __init__(self, owner, address, size):
        super(uEmuMemoryView, self).__init__()
        self.owner = owner
        self.viewid = address
        self.address = address
        self.size = size
        self.lastContent = []

    def Create(self, title):

        if not simplecustviewer_t.Create(self, title):
            return False

        return True

    def SetContent(self, context):

        self.ClearLines()
        
        if context is None:
            return

        memory = context.mem_read(self.address, self.size)

        size = len(memory)

        hdr_title = COLSTR("  Memory at [ ", SCOLOR_AUTOCMT)
        hdr_title += COLSTR("0x%X: %d byte(s)" % (self.address, size), SCOLOR_DREF)
        hdr_title += COLSTR(" ]", SCOLOR_AUTOCMT)
        self.AddLine(str(hdr_title))
        self.AddLine("")
        self.AddLine(COLSTR("                0  1  2  3  4  5  6  7  8  9  A  B  C  D  E  F", SCOLOR_AUTOCMT))

        startAddress = self.address
        line = ""
        chars = ""
        get_char = lambda byte: chr(byte) if 0x20 <= byte <= 0x7E else '.'

        if size != 0:
            for x in range(size):
                if x%16==0:
                    line += COLSTR(" %.12X: " % startAddress, SCOLOR_AUTOCMT)
                if len(self.lastContent) == len(memory):
                    if memory[x] != self.lastContent[x]:
                        line += COLSTR(str("%.2X " % memory[x]), SCOLOR_VOIDOP)
                        chars += COLSTR(get_char(memory[x]), SCOLOR_VOIDOP)
                    else:
                        line += COLSTR(str("%.2X " % memory[x]), SCOLOR_NUMBER)
                        chars += COLSTR(get_char(memory[x]), SCOLOR_NUMBER)
                else:
                    line += COLSTR(str("%.2X " % memory[x]), SCOLOR_NUMBER)
                    chars += COLSTR(get_char(memory[x]), SCOLOR_NUMBER)

                if (x+1)%16==0:
                    line += "  " + chars
                    self.AddLine(line)
                    startAddress += 16
                    line = ""
                    chars = ""

            # add padding
            tail = 16 - size%16
            if tail != 0:
                for x in range(tail): line += "   "
                line += "  " + chars
                self.AddLine(line)

        self.Refresh()
        self.lastContent = memory

    def OnClose(self):
        self.owner.memory_view_closed(self.viewid)

# === uEmuControlView

class uEmuControlView(PluginForm):
    def __init__(self, owner):
        self.owner = owner
        PluginForm.__init__(self)

    def OnCreate(self, form):
        self.parent = self.FormToPyQtWidget(form)
        self.PopulateForm()

    def PopulateForm(self):
        btnStart = QPushButton("Start")
        btnRun = QPushButton("Run")
        btnStep = QPushButton("Step")
        btnStop = QPushButton("Stop")

        btnStart.clicked.connect(self.OnEmuStart)
        btnRun.clicked.connect(self.OnEmuRun)
        btnStep.clicked.connect(self.OnEmuStep)
        btnStop.clicked.connect(self.OnEmuStop)

        hbox = QHBoxLayout()
        hbox.setAlignment(QtCore.Qt.AlignCenter)
        hbox.addWidget(btnStart)
        hbox.addWidget(btnRun)
        hbox.addWidget(btnStep)
        hbox.addWidget(btnStop)

        self.parent.setLayout(hbox) 

    def OnEmuStart(self, code=0):
        self.owner.emu_start()

    def OnEmuRun(self, code=0):
        self.owner.emu_run()

    def OnEmuStep(self, code=0):
        self.owner.emu_step()

    def OnEmuStop(self, code=0):
        self.owner.emu_stop()

    def OnClose(self, form):
        self.owner.contol_view_closed()

# === uEmuMappeduMemoryView

class uEmuMappeduMemoryView(IDAAPI_Choose):

    def __init__(self, owner, memory, flags=0, width=None, height=None, embedded=False):
        IDAAPI_Choose.__init__(
            self,
            "uEmu Mapped Memory",
            [ ["Start", 20], ["End", 20], ["Permissions", 10] ],
            flags = flags,
            width = width,
            height = height,
            embedded = embedded)
        self.n = 0
        self.items = memory
        self.icon = -1
        self.selcount = 0
        self.popup_names = [ "", "Dump To File", "Show", "" ]
        self.owner = owner

    def OnClose(self):
        pass

    def OnDeleteLine(self, n): # Save JSON
        filePath = IDAAPI_AskFile(1, "*.bin", "Dump memory")
        if filePath is not None:
            with open(filePath, 'w') as outfile:
                address = self.items[n][0]
                size = self.items[n][1] - self.items[n][0] + 1
                outfile.seek(0, 0)
                outfile.write(self.owner.unicornEngine.get_mapped_bytes(address, size))
                outfile.close()
        return n

    def OnEditLine(self, n):
        address = self.items[n][0]
        size = self.items[n][1] - self.items[n][0] + 1
        self.owner.show_memory(address, size)

    def OnGetLine(self, n):
        return [ 
            "0x%X" % self.items[n][0], 
            "0x%X" % self.items[n][1], 
            "%c%c%c" % (
                "r" if self.items[n][2] & UC_PROT_READ else "-",
                "w" if self.items[n][2] & UC_PROT_WRITE else "-",
                "x" if self.items[n][2] & UC_PROT_EXEC else "-")
        ] 

    def OnGetSize(self):
        n = len(self.items)
        return n

    def show(self):
        return self.Show(True) >= 0

class uEmuOnMainCallable(object):
    def __init__(self, handler):
        self.handler = handler
    def __call__(self):
        return self.handler()

# === Settings

class uEmuSettingsDialog(Form):
    def __init__(self):
        Form.__init__(self, r"""STARTITEM {id:chk_followpc}
BUTTON YES* Save
BUTTON CANCEL Cancel
uEmu Settings
<Follow PC:{chk_followpc}>
<Convert to Code automatically:{chk_forcecode}>
<Trace instructions:{chk_trace}>{emu_group}>
""", {
        'emu_group': Form.ChkGroupControl(("chk_followpc", "chk_forcecode", "chk_trace")),
        })

# === uEmuUnicornEngine

class uEmuRegisterValueDialog(Form):
    def __init__(self, regName):
        Form.__init__(self, r"""STARTITEM {id:reg_val}
BUTTON YES* Save
BUTTON CANCEL Cancel
Register Value
{reg_label}
<##:{reg_val}>
""", {
        'reg_label': Form.StringLabel("Enter hex value for [ " + regName + " ]"),
        'reg_val': Form.NumericInput(tp=Form.FT_HEX, swidth=20)
        })

class uEmuRegisterValue128Dialog(Form):
    def __init__(self, regName):
        Form.__init__(self, r"""STARTITEM {id:reg_valh}
BUTTON YES* Save
BUTTON CANCEL Cancel
Register Value
{reg_label}
<##High\::{reg_valh}>
<##Low\: :{reg_vall}>
""", {
        'reg_label': Form.StringLabel("Enter hex value for [ " + regName + " ]"),
        'reg_valh': Form.NumericInput(tp=Form.FT_HEX, swidth=20),
        'reg_vall': Form.NumericInput(tp=Form.FT_HEX, swidth=20)
        })

class uEmuMapBinaryFileDialog(Form):
    def __init__(self, address):
        Form.__init__(self, r"""STARTITEM {id:file_name}
BUTTON YES* Map
BUTTON CANCEL Cancel
Map Binary File
{form_change_cb}
<#Select file to open#File\::{file_name}>
{note_label}
<##Address\:    :{mem_addr}>
<##Data Offset\::{mem_offset}>
<##Data Size\:  :{mem_size}> {total_label}

""", {
        'file_name': Form.FileInput(swidth=50, open=True),
        'note_label': Form.StringLabel("NOTE: Region MUST cover 0x%X" % (address)),
        'mem_addr': Form.NumericInput(swidth=20, tp=Form.FT_HEX),
        'mem_offset': Form.NumericInput(swidth=20, tp=Form.FT_HEX),
        'mem_size': Form.NumericInput(swidth=20, tp=Form.FT_HEX),
        'total_label': Form.StringLabel(""),
        'form_change_cb': Form.FormChangeCb(self.OnFormChange)
        })

    def OnFormChange(self, fid):
        if fid == self.file_name.id:
            filepath = self.GetControlValue(self.file_name)
            if os.path.isfile(filepath):
                size = os.path.getsize(filepath)
                self.SetControlValue(self.mem_size, size)
                self.SetControlValue(self.total_label, "Total: %d (0x%X)" % (size, size))
        return 1


class uEmuContextInitDialog(IDAAPI_Choose):

    def __init__(self, regs, flags=0, width=None, height=None, embedded=False):
        IDAAPI_Choose.__init__(
            self,
            "uEmu CPU Context Edit",
            [ ["Register", 10], ["Value", 30] ],
            flags = flags,
            width = width,
            height = height,
            embedded = embedded)
        self.n = 0
        self.items = regs
        self.icon = -1
        self.selcount = 0
        self.popup_names = [ "Load CPU Context", "Save CPU Context", "Edit Register Value", "" ]

    def OnClose(self):
        pass

    def OnInsertLine(self, n=0): # load JSON
        filePath = IDAAPI_AskFile(0, "*.json", "Load CPU Context")
        if filePath is not None:
            with open(filePath, 'r') as infile:
                self.items = json.load(infile)
                infile.close()
                self.Refresh()

    def OnDeleteLine(self, n): # Save JSON
        filePath = IDAAPI_AskFile(1, "*.json", "Save CPU Context")
        if filePath is not None:
            with open(filePath, 'w') as outfile:
                json.dump(self.items, outfile)
                outfile.close()
        return n

    def OnEditLine(self, n):
        if self.items[n][2] == 128:
            regDlg = uEmuRegisterValue128Dialog(self.items[n][0])
            regDlg.Compile()
            regDlg.reg_valh.value = int(self.items[n][1], 0) >> 64
            regDlg.reg_vall.value = int(self.items[n][1], 0)
            ok = regDlg.Execute()
            if ok == 1:
                newValue = (regDlg.reg_valh.value << 64) | regDlg.reg_vall.value
                self.items[n][1] = "0x%X" % newValue
        else:
            regDlg = uEmuRegisterValueDialog(self.items[n][0])
            regDlg.Compile()
            regDlg.reg_val.value = int(self.items[n][1], 0)
            ok = regDlg.Execute()
            if ok == 1:
                newValue = regDlg.reg_val.value
                self.items[n][1] = "0x%X" % newValue
        self.Refresh()

    def OnGetLine(self, n):
        return [ self.items[n][0], self.items[n][1] ] 

    def OnGetSize(self):
        n = len(self.items)
        return n

    def show(self):
        return self.Show(True) >= 0

class uEmuUnicornEngine(object):

    mu = None
    pc = BADADDR

    emuActive       = False
    emuRunning      = False
    emuThread       = None

    kStepCount_Run  = 0
    emuStepCount    = 1

    fix_context     = None
    extended        = False

    def __init__(self, owner):
        super(uEmuUnicornEngine, self).__init__()
        self.owner = owner

        uc_setup = {
            "x64"       : [ UC_X86_REG_RIP,     UC_ARCH_X86,    UC_MODE_64                              ],
            "x86"       : [ UC_X86_REG_EIP,     UC_ARCH_X86,    UC_MODE_32                              ],
            "arm64be"   : [ UC_ARM64_REG_PC,    UC_ARCH_ARM64,  UC_MODE_ARM     | UC_MODE_BIG_ENDIAN    ],
            "arm64le"   : [ UC_ARM64_REG_PC,    UC_ARCH_ARM64,  UC_MODE_ARM     | UC_MODE_LITTLE_ENDIAN ],
            "armbe"     : [ UC_ARM_REG_PC,      UC_ARCH_ARM,    UC_MODE_ARM     | UC_MODE_BIG_ENDIAN    ],
            "armle"     : [ UC_ARM_REG_PC,      UC_ARCH_ARM,    UC_MODE_ARM     | UC_MODE_LITTLE_ENDIAN ],
            "mips64be"  : [ UC_MIPS_REG_PC,     UC_ARCH_MIPS,   UC_MODE_MIPS64  | UC_MODE_BIG_ENDIAN    ],
            "mips64le"  : [ UC_MIPS_REG_PC,     UC_ARCH_MIPS,   UC_MODE_MIPS64  | UC_MODE_LITTLE_ENDIAN ],
            "mipsbe"    : [ UC_MIPS_REG_PC,     UC_ARCH_MIPS,   UC_MODE_MIPS32  | UC_MODE_BIG_ENDIAN    ],
            "mipsle"    : [ UC_MIPS_REG_PC,     UC_ARCH_MIPS,   UC_MODE_MIPS32  | UC_MODE_LITTLE_ENDIAN ],
        }
        arch = UEMU_HELPERS.get_arch()

        self.uc_reg_pc = uc_setup[arch][0]
        self.uc_arch = uc_setup[arch][1]
        self.uc_mode = uc_setup[arch][2]
        uemu_log("Unicorn version [ %s ]" % (unicorn.__version__))
        uemu_log("CPU arch set to [ %s ]" % (arch))

    def is_active(self):
        return self.emuActive

    def is_running(self):
        return self.emuRunning

    def get_context(self):
        # Looks like unicorn context is not serializable in python
        # return self.mu.context_save()

        uc_context = {}
        reg_map = UEMU_HELPERS.get_register_map(UEMU_HELPERS.get_arch())
        reg_ext_map = UEMU_HELPERS.get_register_ext_map(UEMU_HELPERS.get_arch())
        uc_context["cpu"] = [ [ row[1], self.mu.reg_read(row[1]) ] for row in reg_map ]
        uc_context["cpu_ext"] = [ [ row[1], self.mu.reg_read(row[1]) ] for row in reg_ext_map ]
        uc_context["mem"] = [ [ memStart, memEnd, memPerm, self.mu.mem_read(memStart, memEnd - memStart + 1) ] for (memStart, memEnd, memPerm) in self.mu.mem_regions() ]
       
        return uc_context

    def set_context(self, context):
        # Looks like unicorn context is not serializable in python
        # self.mu.context_restore(context)

        for reg in context["cpu"]:
            self.mu.reg_write(reg[0], reg[1])

        for reg in context["cpu_ext"]:
            self.mu.reg_write(reg[0], reg[1])

        for mem in context["mem"]:
            try:
                memStart = mem[0]
                memEnd = mem[1]
                memPerm = mem[2]
                memData = mem[3]
                uemu_log("  map [%X:%X]" % (memStart, memEnd))
                self.mu.mem_map(memStart, memEnd - memStart + 1, memPerm)
                self.mu.mem_write(memStart, str(memData))
            except UcError as e:
                uemu_log("! <U> %s" % e)

        self.pc = self.mu.reg_read(self.uc_reg_pc)
        IDAAPI_SetColor(self.pc, CIC_ITEM, UEMU_CONFIG.IDAViewColor_PC)

        if not self.emuActive:
            if self.owner.trace_inst():
                bytes = IDAAPI_GetBytes(self.pc, IDAAPI_NextHead(self.pc) - self.pc)
                bytes_hex = " ".join("{:02X}".format(ord(c)) for c in bytes)
                uemu_log("* TRACE<I> 0x%X | %-16s | %s" % (self.pc, bytes_hex, IDAAPI_GetDisasm(self.pc, 0)))

            self.emuActive = True

    def is_memory_mapped(self, address):
        try:
            self.mu.mem_read(address, 1)
            return True
        except UcError as e:
            return False

    def jump_to_pc(self):
        IDAAPI_Jump(self.pc)

    def map_memory(self, address, size):
        # - size is unsigned and must be != 0
        # - starting address must be aligned to 4KB
        # - map size must be multiple of the page size (4KB)
        # - permissions can only contain READ or WRITE perms
        try:
            memStart = address
            memEnd = address + size
            memStartAligned = UEMU_HELPERS.ALIGN_PAGE_DOWN(memStart)
            memEndAligned = UEMU_HELPERS.ALIGN_PAGE_UP(memEnd)
            ret = "  map [%X:%X] -> [%X:%X]" % (memStart, memEnd - 1, memStartAligned, memEndAligned - 1)
            uemu_log(ret)
            self.mu.mem_map(memStartAligned, memEndAligned - memStartAligned)
        except UcError as e:
            uemu_log("! <U> %s" % e)
        return ret

    def map_empty(self, address, size):
        self.map_memory(address, size)
        self.mu.mem_write(UEMU_HELPERS.ALIGN_PAGE_DOWN(address), "\x00" * UEMU_CONFIG.UnicornPageSize)

    def map_binary(self, address, size, binaryFile=None):
        binMapDlg = uEmuMapBinaryFileDialog(address)
        binMapDlg.Compile()
        
        ok = binMapDlg.Execute()
        if ok != 1:
            return False
        bin_name = ""
        if(binaryFile):
            bin_name = binaryFile
        else:
            bin_name = binMapDlg.file_name.value
        with open(bin_name, 'rb') as file:
            file.seek(0, 2)
            file_size = file.tell()

            mem_addr = binMapDlg.mem_addr.value
            mem_offset = binMapDlg.mem_offset.value
            mem_size = binMapDlg.mem_size.value

            # check if file is big enough
            if mem_offset + mem_size > file_size:
                file.close()
                return False

            # validate range
            if address < UEMU_HELPERS.ALIGN_PAGE_DOWN(mem_addr):
                return False
            if (address + size) > (mem_addr + mem_size):
                return False

            # read data from file
            file.seek(mem_offset, 0)
            data = file.read(mem_size)
            file.close()

            try:
                self.map_memory(mem_addr, mem_size)
                self.mu.mem_write(mem_addr, data)
            except UcError as e:
                return False

            return True

        return False

    def copy_inited_data(self, start, end):
        if IDAAPI_IsLoaded(start):
            find_inited = False
        else:
            find_inited = True
        ptr = IDAAPI_NextThat(start, -1, UEMU_HELPERS.InitedCallable() if find_inited else UEMU_HELPERS.UninitedCallable())
        while ptr < end:
            if not find_inited:
                # found uninitialized
                tmp_end = end if ptr > end else ptr
                uemu_log("  cpy [%X:%X]" % (start, tmp_end - 1))
                self.mu.mem_write(start, IDAAPI_GetBytes(start, tmp_end - start))
            else:
                # found initialized
                uemu_log("  skp [%X:%X]" % (start, ptr - 1))
                start = ptr

            find_inited = not find_inited
            ptr = IDAAPI_NextThat(ptr, -1, UEMU_HELPERS.InitedCallable() if find_inited else UEMU_HELPERS.UninitedCallable())
        # process tail if any
        if not find_inited:
            tmp_end = end if ptr > end else ptr
            uemu_log("  cpy [%X:%X]" % (start, tmp_end - 1))
            self.mu.mem_write(start, IDAAPI_GetBytes(start, tmp_end - start))

    def fetch_segments(self):
        uemu_log("Fetching segments...")        
        for segEA in Segments():
            segStart = IDAAPI_SegStart(segEA)
            segEnd = IDAAPI_SegEnd(segEA)
            found = False
            overlap_start = False
            overlap_end = False
            tmp = 0
            for (memStart, memEnd, memPerm) in self.mu.mem_regions():
                memEnd = memEnd + 1
                if segStart >= memStart and segEnd <= memEnd:
                    found = True
                    break
                if segStart >= memStart and segStart < memEnd:
                    overlap_start = True
                    tmp = memEnd
                    break
                if segEnd >= memStart and segEnd < memEnd:
                    overlap_end = True
                    tmp = memStart
                    break

            if found:               # already mapped
                continue
            elif overlap_start:     # partial overlap (start)
                self.map_memory(tmp, segEnd - tmp)
                self.copy_inited_data(tmp, segEnd)
            elif overlap_end:       # patrial overlap (end)
                self.map_memory(segStart, tmp - segStart)
                self.copy_inited_data(segStart, tmp)
            else:                   # not found
                self.map_memory(segStart, segEnd - segStart)
                self.copy_inited_data(segStart, segEnd)

    def get_mapped_memory(self):
        return [ [ memStart, memEnd, memPerm ] for (memStart, memEnd, memPerm) in self.mu.mem_regions() ]

    def get_mapped_bytes(self, address, size):
        return self.mu.mem_read(address, size)

    def hook_mem_access(self, uc, access, address, size, value, user_data):
        def bpt_sync_check():
            return self.is_breakpoint_reached(address)
        if execute_sync(uEmuOnMainCallable(bpt_sync_check), MFF_READ):
            # vvv Workaround to fix issue when register are still updated even if emu_stop is called
            self.fix_context = self.mu.context_save()
            # ^^^
            uc.emu_stop()
            def result_handler():
                uemu_log("Memory breakpoint [0x%X] reached from 0x%X : %s" % (address, self.pc, UEMU_HELPERS.trim_spaces(IDAAPI_GetDisasm(self.pc, 0))))

                IDAAPI_SetColor(self.pc, CIC_ITEM, UEMU_CONFIG.IDAViewColor_PC)
                self.owner.update_context(self.pc, self.mu)

                if self.owner.follow_pc():
                    self.jump_to_pc()

                self.emuStepCount = 1
                return True

            execute_sync(uEmuOnMainCallable(result_handler), MFF_WRITE)

    def hook_mem_invalid(self, uc, access, address, size, value, user_data):
        def result_handler():
            uemu_log("! <M> Missing memory at 0x%x, data size = %u, data value = 0x%x" %(address, size, value))
            if(IDAAPI_SegStart(address) != -1): # XXX nemo this means the mapping is in IDA and we should JIT load it.
                uemu_log("Mapping segment...")
                segStart = IDAAPI_SegStart(address)
                segEnd = IDAAPI_SegEnd(address)
                endAligned = UEMU_HELPERS.ALIGN_PAGE_UP(segEnd)
                uemu_log("* seg [%X:%X]" % (segStart, segEnd))
                self.map_memory(segStart, endAligned - segStart)
                self.copy_inited_data(segStart, segEnd) 
                return True
            while True:
                ok = IDAAPI_AskYN(1, "Memory [%X] is not mapped!\nDo you want to map it?\n   YES - Load Binary\n   NO - Fill page with zeroes\n   Cancel - Stop Emulation" % (address))
                if ok == 0:
                    self.map_empty(address, size)
                    break
                elif ok == 1:
                    if self.map_binary(address, size):
                        break
                else:
                    self.interrupt()
                    break
            return True

        execute_sync(uEmuOnMainCallable(result_handler), MFF_WRITE)
        return True

    def init_cpu_context(self, pc):
        self.extended = False
        
        # enable ARMv7 VFP
        if UEMU_HELPERS.get_arch() in ["armle", "armbe"]: 
            if IDAAPI_AskYN(1, "Enable VFP instruction emulation?") == 1:
                tmp_val = self.mu.reg_read(UC_ARM_REG_C1_C0_2)
                tmp_val = tmp_val | (0xf << 20)
                self.mu.reg_write(UC_ARM_REG_C1_C0_2, tmp_val)
                enable_vfp = 0x40000000
                self.mu.reg_write(UC_ARM_REG_FPEXC, enable_vfp)
                self.extended = True
                uemu_log("VFP enabled")
        # enable ARMv8 FP/SIMD
        if UEMU_HELPERS.get_arch() in ["arm64le", "arm64be"]: 
            if IDAAPI_AskYN(1, "Enable FP/SIMD instruction emulation?") == 1:
                cpacr = self.mu.reg_read(UC_ARM64_REG_CPACR_EL1)
                cpacr = cpacr | (0x3 << 20)
                self.mu.reg_write(UC_ARM64_REG_CPACR_EL1, cpacr)
                self.extended = True
                uemu_log("FP/SIMD enabled")

        reg_map = UEMU_HELPERS.get_register_map(UEMU_HELPERS.get_arch())
        regs = [ [ row[0], "0x0", UEMU_HELPERS.get_register_bits(UEMU_HELPERS.get_arch()) ] for row in reg_map ]
        regs_len = len(regs)
        if self.extended:
            reg_ext_map = UEMU_HELPERS.get_register_ext_map(UEMU_HELPERS.get_arch())
            regs = regs + [ [ row[0], "0x0", UEMU_HELPERS.get_register_ext_bits(UEMU_HELPERS.get_arch()) ] for row in reg_ext_map ]

        cpuContext = uEmuContextInitDialog(regs)
        ok = cpuContext.show()
        if ok:
            for idx, val in enumerate(cpuContext.items[0:regs_len-1]):
                self.mu.reg_write(reg_map[idx][1], int(val[1], 0))
            self.mu.reg_write(self.uc_reg_pc, pc)
            if self.extended:
                for idx, val in enumerate(cpuContext.items[regs_len:]):
                    self.mu.reg_write(reg_ext_map[idx][1], int(val[1], 0))
            return True
        else:
            return False

    def set_cpu_context(self):
        reg_map = UEMU_HELPERS.get_register_map(UEMU_HELPERS.get_arch())
        regs = [ [ row[0], "0x%X" % self.mu.reg_read(row[1]), UEMU_HELPERS.get_register_bits(UEMU_HELPERS.get_arch()) ] for row in reg_map ]
        regs_len = len(regs)
        if self.extended:
            reg_ext_map = UEMU_HELPERS.get_register_ext_map(UEMU_HELPERS.get_arch())
            regs = regs + [ [ row[0], "0x%X" % self.mu.reg_read(row[1]), UEMU_HELPERS.get_register_ext_bits(UEMU_HELPERS.get_arch()) ] for row in reg_ext_map ]

        cpuContext = uEmuContextInitDialog(regs)
        ok = cpuContext.show()
        if ok:
            for idx, val in enumerate(cpuContext.items[0:regs_len-1]):
                self.mu.reg_write(reg_map[idx][1], int(val[1], 0))
            if self.extended:
                for idx, val in enumerate(cpuContext.items[regs_len:]):
                    self.mu.reg_write(reg_ext_map[idx][1], int(val[1], 0))
            return True
        else:
            return False

    def run_from(self, address):
        self.mu = Uc(self.uc_arch, self.uc_mode)
        self.mu.hook_add(UC_HOOK_MEM_READ_UNMAPPED | UC_HOOK_MEM_WRITE_UNMAPPED, self.hook_mem_invalid)
        self.mu.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, self.hook_mem_access)
        self.pc = address

        try:
            if self.init_cpu_context(address) == False:
                return

            uemu_log("Mapping segments...")
            segStart = IDAAPI_SegStart(address)
            segEnd = IDAAPI_SegEnd(address)
            endAligned = UEMU_HELPERS.ALIGN_PAGE_UP(segEnd)
            uemu_log("* seg [%X:%X]" % (segStart, segEnd))
            self.map_memory(segStart, endAligned - segStart)
            self.copy_inited_data(segStart, segEnd) 

            #     uemu_log("* seg [%X:%X]" % (segStart, segEnd))
            # XXX nemo: This is where all the regions in the IDB are loaded initially. Going to only map the one responsible for 
            # executing PC and then map the rest JIT as needed.
            # get all segments and merge neighbours
            # lastAddress = BADADDR
            # for segEA in Segments():
            #     segStart = IDAAPI_SegStart(segEA)
            #     segEnd = IDAAPI_SegEnd(segEA)
            #     endAligned = UEMU_HELPERS.ALIGN_PAGE_UP(segEnd)

            #     # merge with provious if
            #     # - we have mapped some segments already
            #     # - aligned old segment is overlapping new segment
            #     # otherwise map new

            #     uemu_log("* seg [%X:%X]" % (segStart, segEnd))
            #     if lastAddress != BADADDR and lastAddress > segStart:
            #         if lastAddress < segEnd:
            #             self.map_memory(lastAddress, endAligned - lastAddress)
            #     else:
            #         self.map_memory(segStart, endAligned - segStart)

            #     # copy initialized bytes
            #     self.copy_inited_data(segStart, segEnd)                

            #     lastAddress = endAligned

            if self.owner.trace_inst():
                bytes = IDAAPI_GetBytes(self.pc, IDAAPI_NextHead(self.pc) - self.pc)
                bytes_hex = " ".join("{:02X}".format(ord(c)) for c in bytes)
                uemu_log("* TRACE<I> 0x%X | %-16s | %s" % (self.pc, bytes_hex, IDAAPI_GetDisasm(self.pc, 0)))

            IDAAPI_SetColor(self.pc, CIC_ITEM, UEMU_CONFIG.IDAViewColor_PC)
            self.emuActive = True

        except UcError as e:
            uemu_log("! <U> %s" % e)

    def step_thread_main(self):
        try:
            self.mu.emu_start(self.pc | 1 if UEMU_HELPERS.is_thumb_ea(self.pc) else self.pc, -1, count=1)
            # vvv Workaround to fix issue when registers are still updated even if emu_stop is called
            if self.fix_context is not None:
                self.mu.context_restore(self.fix_context)
                self.fix_context = None
            # ^^^
            def result_handler():
                self.pc = self.mu.reg_read(self.uc_reg_pc)
                if self.owner.trace_inst():
                    bytes = IDAAPI_GetBytes(self.pc, IDAAPI_NextHead(self.pc) - self.pc)
                    bytes_hex = " ".join("{:02X}".format(ord(c)) for c in bytes)
                    uemu_log("* TRACE<I> 0x%X | %-16s | %s" % (self.pc, bytes_hex, IDAAPI_GetDisasm(self.pc, 0)))

                if not IDAAPI_IsCode(IDAAPI_GetFlags(self.pc)) and self.owner.force_code():
                    uemu_log("Creating code at 0x%X" % (self.pc))
                    IDAAPI_DelItems(self.pc, DOUNK_SIMPLE)
                    IDAAPI_MakeCode(self.pc)

                if self.emuStepCount != 1:
                    if not self.is_breakpoint_reached(self.pc):
                        if self.emuStepCount == self.kStepCount_Run:
                            self.step(self.kStepCount_Run)
                        else:
                            self.step(self.emuStepCount - 1)
                        return
                    else:
                        uemu_log("Breakpoint reached at 0x%X : %s" % (self.pc, UEMU_HELPERS.trim_spaces(IDAAPI_GetDisasm(self.pc, 0))))

                if not self.emuRunning:
                    uemu_log("Emulation interrupted at 0x%X : %s" % (self.pc, UEMU_HELPERS.trim_spaces(IDAAPI_GetDisasm(self.pc, 0))))

                # emulation stopped - update UI
                IDAAPI_SetColor(self.pc, CIC_ITEM, UEMU_CONFIG.IDAViewColor_PC)

                self.owner.update_context(self.pc, self.mu)

                if self.owner.follow_pc():
                    self.jump_to_pc()

                self.emuStepCount = 1
                self.emuRunning = False
                return True

            execute_sync(uEmuOnMainCallable(result_handler), MFF_WRITE)

        except UcError as e:

            def result_handler():
                if self.emuRunning:
                    disasm = UEMU_HELPERS.trim_spaces(IDAAPI_GetDisasm(self.pc, 0))
                    if IDAAPI_AskYN(1, "Unhandled exception:\n   %s\nat:\n   0x%X: %s\n\nDo you want to skip this instruction?" % (e, self.pc, disasm)) != 1:
                        IDAAPI_SetColor(self.pc, CIC_ITEM, UEMU_CONFIG.IDAViewColor_PC)

                        if self.owner.follow_pc():
                            self.jump_to_pc()

                        self.emuRunning = False
                        return

                    self.pc = IDAAPI_NextHead(self.pc)
                    uemu_log("! <U> Unable to emulate [ %s ] - SKIP to 0x%X" % (disasm, self.pc))

                if self.emuStepCount != 1:
                    if not self.is_breakpoint_reached(self.pc):
                        if self.emuStepCount == self.kStepCount_Run:
                            self.step(self.kStepCount_Run)
                        else:
                            self.step(self.emuStepCount - 1)
                        return
                    else:
                        uemu_log("Breakpoint reached at 0x%X : %s" % (self.pc, UEMU_HELPERS.trim_spaces(IDAAPI_GetDisasm(self.pc, 0))))

                if not self.emuRunning:
                    uemu_log("Emulation interrupted at 0x%X : %s" % (self.pc, UEMU_HELPERS.trim_spaces(IDAAPI_GetDisasm(self.pc, 0))))

                # emulation stopped - update UI
                IDAAPI_SetColor(self.pc, CIC_ITEM, UEMU_CONFIG.IDAViewColor_PC)

                self.owner.update_context(self.pc, self.mu)

                if self.owner.follow_pc():
                    self.jump_to_pc()

                self.emuStepCount = 1
                self.emuRunning = False
                return True

            execute_sync(uEmuOnMainCallable(result_handler), MFF_WRITE)

    def step(self, count=1):
        self.emuStepCount = count
        IDAAPI_SetColor(self.pc, CIC_ITEM, UEMU_CONFIG.IDAViewColor_Reset)

        self.emuThread = threading.Thread(target=self.step_thread_main)
        self.emuRunning = True
        self.emuThread.start()

    def run(self):
        self.step(self.kStepCount_Run)

    def interrupt(self):
        IDAAPI_SetColor(self.pc, CIC_ITEM, UEMU_CONFIG.IDAViewColor_Reset)
        self.mu.emu_stop()

        self.emuStepCount = 1
        self.emuRunning = False

    def is_breakpoint_reached(self, address):
        for idx in range(IDAAPI_GetBptQty()):
            if IDAAPI_GetBptEA(idx) == address and IDAAPI_GetBptAttr(address, BPTATTR_FLAGS) & BPT_ENABLED:
                return True
        return False

    def reset(self):
        self.interrupt()
        # unmap all regions
        for (start, end, perm) in self.mu.mem_regions():
            uemu_log("  unmap [%X:%X]" % (start, end))
            self.mu.mem_unmap(start, end - start + 1)

        IDAAPI_SetColor(self.pc, CIC_ITEM, UEMU_CONFIG.IDAViewColor_Reset)    
        self.pc = BADADDR
        self.mu = None
        self.emuActive = False

# === uEmuPlugin

class uEmuPlugin(plugin_t, UI_Hooks):

    popup_menu_hook = None

    flags = PLUGIN_HIDE
    comment = ""

    help = "Tiny cute emulator"
    wanted_name = UEMU_PLUGIN_MENU_NAME
    wanted_hotkey = ""

    # --- PLUGIN DATA

    unicornEngine = None

    settings = {
        "follow_pc"     : False,
        "force_code"    : False,
        "trace_inst"    : False,
    }

    consoleView = None
    controlView = None
    emuInitView = None
    cpuContextView = None
    cpuExtContextView = None
    memoryViews = {}

    # --- PLUGIN LIFECYCLE 

    def init(self):
        super(uEmuPlugin, self).__init__()
        self.hook_ui_actions()
        uemu_log("Init plugin")
        return PLUGIN_KEEP

    def console_window(self):
        # XXX nemo This is a console window for interacting with the emulator
        # in an interactive shell.
        # Commands as follows:
        # === uEmuControlView

        class uEmuConsoleWindow(PluginForm):
            def __init__(self, owner):
                self.owner = owner
                PluginForm.__init__(self)

            def OnCreate(self, form):
                self.parent = self.FormToPyQtWidget(form)
                self.PopulateForm()

            def PopulateForm(self):
                self.prompt = QComboBox() #QLineEdit()
                self.output = QTextEdit("uEmu Debugger") 
                sb = self.output.verticalScrollBar()
                sb.setValue(sb.maximum())
                self.prompt.setEditable(True)
                #self.prompt.setAlignment(QtCore.Qt.AlignBottom)
                #self.output.setAlignment(QtCore.Qt.AlignTop)
                self.output.setReadOnly(True)
                self.prompt.lineEdit().returnPressed.connect(self.SubmitCommand)
 
                hbox = QVBoxLayout()

                hbox.setAlignment(QtCore.Qt.AlignTop)
                hbox.addWidget(self.output)
                hbox.addWidget(self.prompt)
                

                self.parent.setLayout(hbox) 
            
            def log(self,msg):
                self.output.append(msg)

            def cmd_help(self,args=[]): # help message
                text =  "[ uEmu Debugger Help ]\n\n"
                text += "Commands:\n"
                text += "\thelp   - this help screen.\n"
                text += "\tclear  - clear the screen\n"
                text += "\tstart  - start emulator at ScreenEA()\n"
                text += "\tstep   - step a single instruction\n"
                text += "\trun    - run until a fault or breakpoint\n"
                text += "\treset  - reset the emulator\n"
                text += "\tstop   - stop the emulator\n"
                text += "\tshowpc - jump to pc in the ida view\n"
                text += "\tmem    - get list of mapped regions\n"
                text += "\tmm     - map memory at address and fill with \\x00s.\n"
                text += "\t\tusage: mm <address> <size>\n"
                text += "\twd     - write dword (or any sized numerical value) to memory\n"
                text += "\t\tusage: wd <address> <dword>\n"
                text += "\tdd     - dump dwords from memory\n"
                text += "\t\tusage: dd <address> <num>\n"
                self.log(text)

            def cmd_clear(self,args=[]): # clear the output box
                self.output.setText("")

            def cmd_run(self,args=[]): # run emulator
                self.log("Running emulator...\n")
                self.owner.emu_run()

            def cmd_step(self,args=[]): # single step
                self.log("Stepping\n")
                self.owner.emu_step()
            
            def cmd_start(self,args=[]): # start emulator
                self.log("Starting emulator...\n")
                self.owner.emu_start()
        
            def cmd_reset(self,args=[]): # reset emulator
                self.log("Ressetting emulator")
                self.owner.emu_reset()

            def cmd_stop(self,args=[]): # stop emulator
                self.log("Stopping emulator")
                self.owner.emu_stop()

            def cmd_showpc(self,args=[]): # show pc in ida view
                self.log("Jumping to pc in ida view.")
                self.owner.jump_to_pc()

            def cmd_get_mapped_memory(self,args=[]): # get memory
                if not self.owner.unicornEngine.is_active():
                    self.log("Emulator is not active")
                    return

                if self.owner.unicornEngine.is_running():
                    self.log("Emulator is not running")
                    return

                print(self.owner.unicornEngine.get_mapped_memory()) # XXX replace with self.log once we untangle this data
            
            def cmd_map_memory(self,args=[]): # create a mapping (filled with 0s)
                if not self.owner.unicornEngine.is_active():
                    self.log("Emulator is not active")
                    return

                if self.owner.unicornEngine.is_running():
                    self.log("Emulator is not running")
                    return
                
                if(len(args) != 3):
                    self.log("usage: mm <0xaddress> <0xlength>")
                    return

                address = int(args[1],16)
                size = int(args[2],16)
                self.log(self.owner.unicornEngine.map_memory(address,size))

            def cmd_write_dword(self,args=[]): # write dword to memory at address
                if not self.owner.unicornEngine.is_active():
                    self.log("Emulator is not active")
                    return

                if self.owner.unicornEngine.is_running():
                    self.log("Emulator is not running")
                    return

                if(len(args) != 3):
                    self.log("usage: wd <0xaddress> <0xlength>")
                    return

                address = int(args[1],16)
                dword = int(args[2],16)                

                self.owner.unicornEngine.mu.mem_write(address, struct.pack("<L",dword))
                self.log("Wrote dword 0x%x to 0x%lx" % (dword,address))

            def cmd_dump_dwords(self,args=[]): # dump x dwords from address
                if not self.owner.unicornEngine.is_active():
                    self.log("Emulator is not active")
                    return

                if self.owner.unicornEngine.is_running():
                    self.log("Emulator is not running")
                    return

                if(len(args) != 3):
                    self.log("usage: dd <0xaddress> <0xnumber>")
                    return

                address = int(args[1],16)
                num = int(args[2],16)     

                line = ""
                for i in xrange(0,num):
                    line += ("0x%lx" % struct.unpack("<L",uEmu.unicornEngine.mu.mem_read(address,4)))
                    line += " "
                    address += 4 # next
                    if(not(i % 4)): 
                        self.log(line)
                        line =""
                self.log(line)
                    

            def SubmitCommand(self):
                cmd = self.prompt.lineEdit().text()
                self.prompt.lineEdit().setText("")
                cmdarr = []
                if(cmd.find(" ")):
                    cmdarr = cmd.split(" ")
                else:
                    cmdarr = [cmd]
                print "Received command: %s" % cmdarr[0]
                self.log(cmd)
                cmddispatch = {
                    "help"  : self.cmd_help,
                    "clear" : self.cmd_clear,
                    "start" : self.cmd_start,
                    "run"   : self.cmd_run,
                    "step"  : self.cmd_step,
                    "reset" : self.cmd_reset,
                    "stop"  : self.cmd_stop,
                    "showpc": self.cmd_showpc,
                    "mem"   : self.cmd_get_mapped_memory,
                    "mm"    : self.cmd_map_memory,
                    "wd"    : self.cmd_write_dword,
                    "dd"    : self.cmd_dump_dwords
                }
                if(cmddispatch.has_key(cmdarr[0])):
                    cmddispatch[cmdarr[0]](cmdarr) # dispatch menu item
                else:
                    self.output.append("error: Invalid command. %s \"help\" for help\n" % cmdarr[0])
                
        if self.consoleView is None:
            self.consoleView = uEmuConsoleWindow(self)
            self.consoleView.Show("uEmu Console Window")
    
    def run(self, arg):
        uemu_log("Run plugin with (%d)" % arg)

    def run(self, arg = 0):
        uemu_log("Run plugin")
        self.register_menu_actions()
        self.attach_main_menu_actions()
        self.unicornEngine = uEmuUnicornEngine(self)

    def term(self): # asynchronous unload (external, UI_Hook::term)
        self.unicornEngine = None
        self.unhook_ui_actions()
        self.detach_main_menu_actions()
        self.unregister_menu_actions()
        uemu_log("Unload plugin")

    def get_context_columns(self):
        return 2

    def unload_plugin(self): # synchronous unload (internal, Main Menu)
        if self.unicornEngine.is_active():
            self.unicornEngine.reset()
        self.close_windows()
        self.memoryViews.clear()
        self.detach_main_menu_actions()
        self.unregister_menu_actions()

    def do_nothing(self):
        pass

    def ready_to_run(self):
        uemu_log("UI ready. Run plugin")
        self.register_menu_actions()
        self.attach_main_menu_actions()
        self.unicornEngine = uEmuUnicornEngine(self)

    def follow_pc(self):
        return self.settings["follow_pc"]

    def force_code(self):
        return self.settings["force_code"]

    def trace_inst(self):
        return self.settings["trace_inst"]

    def load_project(self):
        filePath = IDAAPI_AskFile(0, "*.emu", "Open eEmu project")
        if filePath is None:
            return
        with open(filePath, 'rb') as file:
            settings = pickle.load(file)
            self.unicornEngine.set_context(pickle.load(file))
            file.close()

            uemu_log("Project loaded from %s" % filePath)

    def save_project(self):
        filePath = IDAAPI_AskFile(1, "*.emu", "Save FridaLink project")
        if filePath is None:
            return
        with open(filePath, 'wb') as file:
            pickle.dump(self.settings, file, pickle.HIGHEST_PROTOCOL)
            pickle.dump(self.unicornEngine.get_context(), file, pickle.HIGHEST_PROTOCOL)
            file.close()
            uemu_log("Project saved to %s" % filePath)

    # --- MAIN MENU

    MENU_ITEMS = []

    def register_new_action(self, act_name, act_text, act_handler, shortcut, tooltip, icon):
        new_action = action_desc_t(
            act_name,       # The action name. This acts like an ID and must be unique
            act_text,       # The action text.
            act_handler,    # The action handler.
            shortcut,       # Optional: the action shortcut
            tooltip,        # Optional: the action tooltip (available in menus/toolbar)
            icon)           # Optional: the action icon (shows when in menus/toolbars)
        register_action(new_action)

    def handle_menu_action(self, action):
        [x.handler() for x in self.MENU_ITEMS if x.action == action]

    def register_menu_actions(self):
        self.MENU_ITEMS.append(UEMU_HELPERS.MenuItem(UEMU_PLUGIN_NAME + ":start",             self.emu_start,             "Start",                      "Start emulation",           None,                   True    ))
        self.MENU_ITEMS.append(UEMU_HELPERS.MenuItem(UEMU_PLUGIN_NAME + ":run",               self.emu_run,               "Run",                        "Run",                       None,                   True    ))
        self.MENU_ITEMS.append(UEMU_HELPERS.MenuItem(UEMU_PLUGIN_NAME + ":console",           self.console_window,        "Console",                    "Console",                   None,                   True    ))
        self.MENU_ITEMS.append(UEMU_HELPERS.MenuItem(UEMU_PLUGIN_NAME + ":step",              self.emu_step,              "Step",                       "Step to next instruction",  "SHIFT+CTRL+ALT+S",     True    ))
        self.MENU_ITEMS.append(UEMU_HELPERS.MenuItem(UEMU_PLUGIN_NAME + ":stop",              self.emu_stop,              "Stop",                       "Stop emulation",            None,                   True    ))
        self.MENU_ITEMS.append(UEMU_HELPERS.MenuItem(UEMU_PLUGIN_NAME + ":reset",             self.emu_reset,             "Reset",                      "Reset emulation",           None,                   True    ))
        self.MENU_ITEMS.append(UEMU_HELPERS.MenuItem("-",                                     self.do_nothing,            "",                           None,                        None,                   True    ))
        self.MENU_ITEMS.append(UEMU_HELPERS.MenuItem(UEMU_PLUGIN_NAME + ":jmp_pc",            self.jump_to_pc,            "Jump to PC",                 "Jump to PC",                "SHIFT+CTRL+ALT+J",     True    ))
        self.MENU_ITEMS.append(UEMU_HELPERS.MenuItem(UEMU_PLUGIN_NAME + ":cng_cpu",           self.change_cpu_context,    "Change CPU Context",         "Change CPU Context",        None,                   True    ))
        self.MENU_ITEMS.append(UEMU_HELPERS.MenuItem("-",                                     self.do_nothing,            "",                           None,                        None,                   True    ))
        self.MENU_ITEMS.append(UEMU_HELPERS.MenuItem(UEMU_PLUGIN_NAME + ":ctl_view",          self.show_controls,         "Show Controls",              "Show Control Window",       None,                   True    ))
        self.MENU_ITEMS.append(UEMU_HELPERS.MenuItem(UEMU_PLUGIN_NAME + ":cpu_view",          self.show_cpu_context,      "Show CPU Context",           "Show CPU Registers",        None,                   True    ))
        self.MENU_ITEMS.append(UEMU_HELPERS.MenuItem(UEMU_PLUGIN_NAME + ":cpu_ext_view",      self.show_cpu_ext_context,  "Show CPU Extended Context",  "Show Extended Registers",   None,                   True    ))
        self.MENU_ITEMS.append(UEMU_HELPERS.MenuItem(UEMU_PLUGIN_NAME + ":mem_view",          self.show_memory,           "Show Memory Range",          "Show Memory Range",         None,                   True    ))
        self.MENU_ITEMS.append(UEMU_HELPERS.MenuItem(UEMU_PLUGIN_NAME + ":mem_map",           self.show_mapped,           "Show Mapped Memory",         "Show Mapped Memory",        None,                   False   ))
        self.MENU_ITEMS.append(UEMU_HELPERS.MenuItem(UEMU_PLUGIN_NAME + ":fetch_segs",        self.fetch_segments,        "Fetch Segments",             "Fetch Segments",            None,                   False   ))
        self.MENU_ITEMS.append(UEMU_HELPERS.MenuItem("-",                                     self.do_nothing,            "",                           None,                        None,                   False   ))
        self.MENU_ITEMS.append(UEMU_HELPERS.MenuItem(UEMU_PLUGIN_NAME + ":load_prj",          self.load_project,          "Load Project",               "Load Project",              None,                   False   ))
        self.MENU_ITEMS.append(UEMU_HELPERS.MenuItem(UEMU_PLUGIN_NAME + ":save_prj",          self.save_project,          "Save Project",               "Save Project",              None,                   False   ))
        self.MENU_ITEMS.append(UEMU_HELPERS.MenuItem(UEMU_PLUGIN_NAME + ":settings",          self.show_settings,         "Settings",                   "Settings",                  None,                   False   ))
        self.MENU_ITEMS.append(UEMU_HELPERS.MenuItem("-",                                     self.do_nothing,            "",                           None,                        None,                   False   ))
        self.MENU_ITEMS.append(UEMU_HELPERS.MenuItem(UEMU_PLUGIN_NAME + ":unload",            self.unload_plugin,         "Unload Plugin",              "Unload Plugin",             None,                   False   ))

        for item in self.MENU_ITEMS:
            if item.action == "-":
                continue
            self.register_new_action(item.action, item.title, UEMU_HELPERS.IdaMenuActionHandler(self, item.action), item.shortcut, item.tooltip,  -1)

    def unregister_menu_actions(self):
        for item in self.MENU_ITEMS:
            if item.action == "-":
                continue
            unregister_action(item.action)

    def attach_main_menu_actions(self):
        for item in self.MENU_ITEMS:
            attach_action_to_menu("Edit/Plugins/" + UEMU_PLUGIN_MENU_NAME + "/" + item.title, item.action, SETMENU_APP)

    def detach_main_menu_actions(self):
        for item in self.MENU_ITEMS:
            detach_action_from_menu("Edit/Plugins/" + UEMU_PLUGIN_MENU_NAME + "/" + item.title, item.action)

    # --- POPUP MENU

    def hook_ui_actions(self):
        self.popup_menu_hook = self
        self.popup_menu_hook.hook()

    def unhook_ui_actions(self):
        if self.popup_menu_hook != None:
            self.popup_menu_hook.unhook()

    # IDA 7.x
    def finish_populating_widget_popup(self, widget, popup_handle):
        if get_widget_type(widget) == BWN_DISASM:
            for item in self.MENU_ITEMS:
                if item.popup:
                    attach_action_to_popup(widget, popup_handle, item.action, UEMU_PLUGIN_MENU_NAME + "/")
    
    # IDA 6.x
    def finish_populating_tform_popup(self, form, popup_handle):
        if get_tform_type(form) == BWN_DISASM:
            for item in self.MENU_ITEMS:
                if item.popup:
                    attach_action_to_popup(form, popup_handle, item.action, UEMU_PLUGIN_MENU_NAME + "/")

    # --- DELEGATES

    def contol_view_closed(self):
        self.controlView = None

    def context_view_closed(self):
        self.cpuContextView = None

    def ext_context_view_closed(self):
        self.cpuExtContextView = None

    def memory_view_closed(self, viewid):
        del self.memoryViews[viewid]

    def update_context(self, address, context):
        # Update CPU Context Views
        if self.cpuContextView is not None:
            self.cpuContextView.SetContent(address, context)
        if self.cpuExtContextView is not None:
            self.cpuExtContextView.SetContent(address, context)

        # Update Memory Views
        for viewid in self.memoryViews:
            self.memoryViews[viewid].SetContent(context)

    # --- METHODS

    def emu_start(self):
        if self.unicornEngine.is_active():
            uemu_log("Emulator is already active")
            return

        if self.unicornEngine.is_running():
            uemu_log("Emulator is running")
            return

        self.unicornEngine.run_from(IDAAPI_ScreenEA())
        uemu_log("Emulation started")

    def emu_run(self):
        if not self.unicornEngine.is_active():
            uemu_log("Emulator is not active")
            return

        if self.unicornEngine.is_running():
            uemu_log("Emulator is running")
            return

        self.unicornEngine.run()

    def emu_step(self):
        if not self.unicornEngine.is_active():
            uemu_log("Emulator is not active")
            return

        if self.unicornEngine.is_running():
            uemu_log("Emulator is running")
            return
        count = 1
        if UEMU_HELPERS.is_alt_pressed():
            count = IDAAPI_AskLong(1, "Enter Step Count")
            if count is None or count < 0: 
                return
            if count == 0: 
                count = 1

        self.unicornEngine.step(count)

    def emu_stop(self):
        if not self.unicornEngine.is_active():
            uemu_log("Emulator is not active")
            return

        if not self.unicornEngine.is_running():
            uemu_log("Emulator is not running")
            return

        self.unicornEngine.interrupt()

    def emu_reset(self):
        if not self.unicornEngine.is_active():
            uemu_log("Emulator is not active")
            return
        
        self.unicornEngine.reset()

        self.close_windows()

        self.memoryViews.clear()
        uemu_log("Emulation reset")

    def jump_to_pc(self):
        if not self.unicornEngine.is_active():
            uemu_log("Emulator is not active")
            return

        if self.unicornEngine.is_running():
            uemu_log("Emulator is running")
            return

        self.unicornEngine.jump_to_pc()

    def change_cpu_context(self):
        if not self.unicornEngine.is_active():
            uemu_log("Emulator is not active")
            return

        if self.unicornEngine.is_running():
            uemu_log("Emulator is running")
            return

        self.unicornEngine.set_cpu_context()
        if self.cpuContextView is not None:
            self.cpuContextView.SetContent(self.unicornEngine.pc, self.unicornEngine.mu)
        if self.cpuExtContextView is not None:
            self.cpuExtContextView.SetContent(self.unicornEngine.pc, self.unicornEngine.mu)

    def show_controls(self):
        if self.controlView is None:
            self.controlView = uEmuControlView(self)
            self.controlView.Show("uEmu Control")

    def show_cpu_context(self):
        if not self.unicornEngine.is_active():
            uemu_log("Emulator is not active")
            return

        if self.cpuContextView is None:
            self.cpuContextView = uEmuCpuContextView(self, False)
            self.cpuContextView.Create("uEmu CPU Context")
            self.cpuContextView.SetContent(self.unicornEngine.pc, self.unicornEngine.mu)
            self.cpuContextView.Show()
            self.cpuContextView.Refresh()

    def show_cpu_ext_context(self):
        if not self.unicornEngine.is_active():
            uemu_log("Emulator is not active")
            return

        if UEMU_HELPERS.get_arch() not in ["arm64le", "arm64be"]:
            return

        if self.cpuExtContextView is None:
            self.cpuExtContextView = uEmuCpuContextView(self, True)
            self.cpuExtContextView.Create("uEmu CPU Extended Context")
            self.cpuExtContextView.SetContent(self.unicornEngine.pc, self.unicornEngine.mu)
            self.cpuExtContextView.Show()
            self.cpuExtContextView.Refresh()

    def show_memory(self, address = 0, size = 16):
        if not self.unicornEngine.is_active():
            uemu_log("Emulator is not active")
            return

        memRangeDlg = uEmuMemoryRangeDialog()
        memRangeDlg.Compile()
        memRangeDlg.mem_addr.value = address
        memRangeDlg.mem_size.value = size
        ok = memRangeDlg.Execute()
        if ok == 1:
            mem_addr = memRangeDlg.mem_addr.value
            mem_size = memRangeDlg.mem_size.value
            mem_cmnt = memRangeDlg.mem_cmnt.value

            if mem_addr not in self.memoryViews:
                if(IDAAPI_SegStart(mem_addr) != -1): # XXX nemo this means the mapping is in IDA and we should JIT load it.
                    uemu_log("Mapping segment...")
                    segStart = IDAAPI_SegStart(mem_addr)
                    segEnd = IDAAPI_SegEnd(mem_addr)
                    endAligned = UEMU_HELPERS.ALIGN_PAGE_UP(segEnd)
                    uemu_log("* seg [%X:%X]" % (segStart, segEnd))
                    self.unicornEngine.map_memory(segStart, endAligned - segStart)
                    self.unicornEngine.copy_inited_data(segStart, segEnd) 
                    return

                if not self.unicornEngine.is_memory_mapped(mem_addr):
                    ok = IDAAPI_AskYN(1, "Memory [%X:%X] is not mapped!\nDo you want to map it?\n   YES - Load Binary\n   NO - Fill page with zeroes\n   Cancel - Close dialog" % (mem_addr, mem_addr + mem_size))
                    if ok == 0:
                        self.unicornEngine.map_empty(mem_addr, mem_size)
                    elif ok == 1:
                        if not self.unicornEngine.map_binary(mem_addr, mem_size):
                            return
                    else:
                        return

                self.memoryViews[mem_addr] = uEmuMemoryView(self, mem_addr, mem_size)
                self.memoryViews[mem_addr].Create("uEmu Memory [ " + mem_cmnt + " ]")
                self.memoryViews[mem_addr].SetContent(self.unicornEngine.mu)
            self.memoryViews[mem_addr].Show()
            self.memoryViews[mem_addr].Refresh()

    def show_mapped(self):
        if not self.unicornEngine.is_active():
            uemu_log("Emulator is not active")
            return

        mappeduEmuMemoryView = uEmuMappeduMemoryView(self, self.unicornEngine.get_mapped_memory())
        mappeduEmuMemoryView.show()

    def fetch_segments(self):
        if not self.unicornEngine.is_active():
            uemu_log("Emulator is not active")
            return

        if self.unicornEngine.is_running():
            uemu_log("Emulator is running")
            return

        self.unicornEngine.fetch_segments()

    def show_settings(self):
        kSettingsMask_FollowPC  = 0x1
        kSettingsMask_ForceCode = 0x2
        kSettingsMask_TraceInst = 0x4

        settingsDlg = uEmuSettingsDialog()
        settingsDlg.Compile()
        settingsDlg.emu_group.value = 0

        if self.settings["follow_pc"]: settingsDlg.emu_group.value = settingsDlg.emu_group.value | kSettingsMask_FollowPC
        if self.settings["force_code"]: settingsDlg.emu_group.value = settingsDlg.emu_group.value | kSettingsMask_ForceCode
        if self.settings["trace_inst"]: settingsDlg.emu_group.value = settingsDlg.emu_group.value | kSettingsMask_TraceInst

        ok = settingsDlg.Execute()
        if ok == 1:
            self.settings["follow_pc"] = True if settingsDlg.emu_group.value & kSettingsMask_FollowPC else False
            self.settings["force_code"] = True if settingsDlg.emu_group.value & kSettingsMask_ForceCode else False
            self.settings["trace_inst"] = True if settingsDlg.emu_group.value & kSettingsMask_TraceInst else False

    def close_windows(self):
        if self.cpuContextView is not None:
            self.cpuContextView.Close()
            self.cpuContextView = None

        if self.cpuExtContextView is not None:
            self.cpuExtContextView.Close()
            self.cpuExtContextView = None

        for viewid in self.memoryViews:
            self.memoryViews[viewid].Close()
            self.memoryViews[viewid] = None

def PLUGIN_ENTRY():
    return uEmuPlugin()

if UEMU_USE_AS_SCRIPT:
    if __name__ == '__main__':
        uEmu = uEmuPlugin()
        uEmu.init()
        uEmu.run()

