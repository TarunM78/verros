"""Desktop GUI for pygeartrain.

Pick a gear train type, choose which member is the input, output and fixed,
set tooth counts and profile parameters, then view the ratio, animate the
mechanism, and export PNG / GIF / CAD curve files.

Run with:  python pygeartrain_gui.py
"""
import os
import sys
import textwrap
import traceback
from typing import Any, Dict, List, Tuple

import numpy as np

import matplotlib
matplotlib.use('TkAgg')
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.backends.backend_agg import FigureCanvasAgg

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from pygeartrain import cad_export, solid_model, step_export
from pygeartrain.specs import SPECS, SPEC_BY_NAME, GearSpec, Param


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class ScrollableFrame(ttk.Frame):
    """A frame whose content scrolls vertically when it is taller than the window."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.scrollbar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.content = ttk.Frame(self.canvas, **kwargs)
        self._window = self.canvas.create_window((0, 0), window=self.content, anchor='nw')
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.content.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self._window, width=e.width))
        self.canvas.bind('<Enter>', lambda e: self.canvas.bind_all('<MouseWheel>', self._on_wheel))
        self.canvas.bind('<Leave>', lambda e: self.canvas.unbind_all('<MouseWheel>'))

    def _on_wheel(self, event):
        bbox = self.canvas.bbox('all')
        if bbox and bbox[3] > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-event.delta / 120), 'units')


class GearTrainApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title('pygeartrain')
        self.scale = max(1.0, root.winfo_fpixels('1i') / 96.0)
        w = int(min(root.winfo_screenwidth() * 0.9, 1320 * self.scale))
        h = int(min(root.winfo_screenheight() * 0.85, 880 * self.scale))
        root.geometry(f'{w}x{h}')
        root.minsize(int(900 * self.scale), int(600 * self.scale))

        self.spec: GearSpec = SPECS[0]
        self.gear = None
        self.param_vars: Dict[str, tk.Variable] = {}
        self.param_widgets: List[tk.Widget] = []
        self.anim_running = False
        self.anim_phase = 0.0
        self.anim_scale = 0.01
        self.anim_job = None

        self._build_layout()
        self._select_spec(self.spec.name)
        self.update_gear()

    # ----- layout -----------------------------------------------------------
    def _build_layout(self):
        style = ttk.Style()
        try:
            style.theme_use('vista' if sys.platform.startswith('win') else 'clam')
        except tk.TclError:
            pass
        style.configure('Warn.TLabel', foreground='#b35c00')
        style.configure('Hint.TLabel', foreground='#666666')
        style.configure('Big.TButton', font=('Segoe UI', 10, 'bold'))

        outer = ttk.Panedwindow(self.root, orient='horizontal')
        outer.pack(fill='both', expand=True)

        panel_width = int(430 * self.scale)
        left = ttk.Frame(outer, padding=6, width=panel_width)
        left.pack_propagate(False)
        right = ttk.Frame(outer)
        outer.add(left, weight=0)
        outer.add(right, weight=1)
        self.root.after(50, lambda: outer.sashpos(0, panel_width))

        self.notebook = ttk.Notebook(left)
        self.notebook.pack(fill='both', expand=True)
        design_scroll = ScrollableFrame(self.notebook, padding=4)
        export_scroll = ScrollableFrame(self.notebook, padding=4)
        self.tab_design = design_scroll
        self.tab_export = export_scroll
        self.notebook.add(design_scroll, text='Design')
        self.notebook.add(export_scroll, text='Animate & Export')

        self.wrap = int(360 * self.scale)
        self._build_design_tab(design_scroll.content)
        self._build_export_tab(export_scroll.content)

        # plot area
        self.fig = Figure(figsize=(7, 7), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=right)
        self.canvas.get_tk_widget().pack(fill='both', expand=True)
        toolbar = NavigationToolbar2Tk(self.canvas, right, pack_toolbar=False)
        toolbar.update()
        toolbar.pack(side='bottom', fill='x')

        self.status = tk.StringVar(value='Ready')
        ttk.Label(self.root, textvariable=self.status, anchor='w', relief='sunken', padding=(6, 2)).pack(side='bottom', fill='x')

        self.root.bind('<Return>', lambda e: self.update_gear())
        self.root.bind('<space>', self._on_space)
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)

    def _build_design_tab(self, parent):
        # gear type and preset
        f = ttk.LabelFrame(parent, text='Gear train', padding=6)
        f.pack(fill='x', pady=(0, 6))
        ttk.Label(f, text='Type').grid(row=0, column=0, sticky='w')
        self.type_var = tk.StringVar(value=self.spec.name)
        cb = ttk.Combobox(f, textvariable=self.type_var, values=[s.name for s in SPECS], state='readonly', width=26)
        cb.grid(row=0, column=1, sticky='ew', padx=(6, 0))
        cb.bind('<<ComboboxSelected>>', lambda e: self._on_type_change())
        self.desc_label = ttk.Label(f, text='', style='Hint.TLabel', wraplength=self.wrap, justify='left')
        self.desc_label.grid(row=1, column=0, columnspan=2, sticky='w', pady=(4, 2))
        ttk.Label(f, text='Preset').grid(row=2, column=0, sticky='w')
        self.preset_var = tk.StringVar()
        self.preset_cb = ttk.Combobox(f, textvariable=self.preset_var, state='readonly', width=26)
        self.preset_cb.grid(row=2, column=1, sticky='ew', padx=(6, 0))
        self.preset_cb.bind('<<ComboboxSelected>>', lambda e: self._apply_preset())
        f.columnconfigure(1, weight=1)

        # kinematics
        f = ttk.LabelFrame(parent, text='Kinematics', padding=6)
        f.pack(fill='x', pady=(0, 6))
        self.kin_vars = {}
        self.kin_combos = {}
        for i, (key, label) in enumerate([('input', 'Input'), ('output', 'Output'), ('fixed', 'Fixed')]):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky='w')
            var = tk.StringVar()
            combo = ttk.Combobox(f, textvariable=var, state='readonly', width=26)
            combo.grid(row=i, column=1, sticky='ew', padx=(6, 0), pady=1)
            self.kin_vars[key] = var
            self.kin_combos[key] = combo
        self.fixed_label = f.grid_slaves(row=2, column=0)[0]
        self.extra_kin_label = ttk.Label(f, text='')
        self.extra_kin_var = tk.StringVar()
        self.extra_kin_combo = ttk.Combobox(f, textvariable=self.extra_kin_var, state='readonly', width=26)
        f.columnconfigure(1, weight=1)
        self.kin_frame = f

        # geometry parameters
        f = ttk.LabelFrame(parent, text='Geometry', padding=6)
        f.pack(fill='x', pady=(0, 6))
        self.param_frame = f
        f.columnconfigure(1, weight=1)

        self.warn_label = ttk.Label(parent, text='', style='Warn.TLabel', wraplength=self.wrap, justify='left')
        self.warn_label.pack(fill='x', pady=(0, 4))

        ttk.Button(parent, text='Update  (Enter)', style='Big.TButton', command=self.update_gear).pack(fill='x', pady=(0, 6))

        # results
        f = ttk.LabelFrame(parent, text='Results', padding=6)
        f.pack(fill='both', expand=True)
        self.result_text = tk.Text(f, height=9, wrap='word', font=('Consolas', 9), relief='flat',
                                   background=self.root.cget('background'))
        self.result_text.pack(fill='both', expand=True)
        self.result_text.configure(state='disabled')

    def _build_export_tab(self, parent):
        # animation
        f = ttk.LabelFrame(parent, text='Animation', padding=6)
        f.pack(fill='x', pady=(0, 6))
        row = ttk.Frame(f)
        row.pack(fill='x')
        self.play_btn = ttk.Button(row, text='Play  (Space)', command=self.toggle_animation)
        self.play_btn.pack(side='left')
        ttk.Button(row, text='Reset', command=self.reset_animation).pack(side='left', padx=(6, 0))
        ttk.Button(row, text='Step', command=self.step_animation).pack(side='left', padx=(6, 0))
        ttk.Label(f, text='Speed').pack(anchor='w', pady=(6, 0))
        self.speed_var = tk.DoubleVar(value=0.0)
        ttk.Scale(f, from_=-2.0, to=2.0, variable=self.speed_var, orient='horizontal').pack(fill='x')
        ttk.Label(f, text='Phase (output rotation, rad)').pack(anchor='w', pady=(6, 0))
        self.phase_var = tk.DoubleVar(value=0.0)
        self.phase_scale = ttk.Scale(f, from_=0.0, to=float(2 * np.pi), variable=self.phase_var,
                                     orient='horizontal', command=self._on_phase_slider)
        self.phase_scale.pack(fill='x')
        self.phase_label = ttk.Label(f, text='0.000 rad', style='Hint.TLabel')
        self.phase_label.pack(anchor='w')

        # images
        f = ttk.LabelFrame(parent, text='Images', padding=6)
        f.pack(fill='x', pady=(0, 6))
        ttk.Button(f, text='Save PNG of current view...', command=self.save_png).grid(row=0, column=0, columnspan=4, sticky='ew')
        ttk.Label(f, text='GIF frames').grid(row=1, column=0, sticky='w', pady=(6, 0))
        self.gif_frames_var = tk.IntVar(value=100)
        ttk.Entry(f, textvariable=self.gif_frames_var, width=8).grid(row=1, column=1, sticky='w', pady=(6, 0))
        ttk.Label(f, text='Total phase (rad)').grid(row=1, column=2, sticky='w', padx=(8, 0), pady=(6, 0))
        self.gif_total_var = tk.DoubleVar(value=round(float(np.pi / 2), 4))
        ttk.Entry(f, textvariable=self.gif_total_var, width=8).grid(row=1, column=3, sticky='w', pady=(6, 0))
        ttk.Button(f, text='Save animated GIF...', command=self.save_gif).grid(row=2, column=0, columnspan=4, sticky='ew', pady=(6, 0))
        self.gif_progress = ttk.Progressbar(f, mode='determinate')
        self.gif_progress.grid(row=3, column=0, columnspan=4, sticky='ew', pady=(4, 0))
        for c in range(4):
            f.columnconfigure(c, weight=1)

        # CAD export
        f = ttk.LabelFrame(parent, text='CAD export (SolidWorks XYZ curves)', padding=6)
        f.pack(fill='x', pady=(0, 6))
        r = 0
        ttk.Label(f, text='Output folder').grid(row=r, column=0, sticky='w')
        self.out_dir_var = tk.StringVar(value=os.path.abspath('output_gui'))
        ttk.Entry(f, textvariable=self.out_dir_var, width=24).grid(row=r, column=1, sticky='ew', padx=(6, 0))
        ttk.Button(f, text='...', width=3, command=self._browse_out_dir).grid(row=r, column=2, padx=(4, 0))
        r += 1
        ttk.Label(f, text='Outer diameter of largest part (mm)').grid(row=r, column=0, sticky='w', pady=(4, 0))
        self.diam_var = tk.DoubleVar(value=70.0)
        ttk.Entry(f, textvariable=self.diam_var, width=10).grid(row=r, column=1, sticky='w', padx=(6, 0), pady=(4, 0))
        r += 1
        ttk.Label(f, text='Face width / thickness (mm)').grid(row=r, column=0, sticky='w', pady=(4, 0))
        self.thick_var = tk.DoubleVar(value=10.0)
        ttk.Entry(f, textvariable=self.thick_var, width=10).grid(row=r, column=1, sticky='w', padx=(6, 0), pady=(4, 0))
        r += 1
        ttk.Label(f, text='Tooth type').grid(row=r, column=0, sticky='w', pady=(4, 0))
        self.tooth_type_var = tk.StringVar(value='herringbone')
        ttk.Combobox(f, textvariable=self.tooth_type_var, values=('spur', 'helix', 'herringbone'),
                     state='readonly', width=12).grid(row=r, column=1, sticky='w', padx=(6, 0), pady=(4, 0))
        r += 1
        ttk.Label(f, text='Helix angle (deg)').grid(row=r, column=0, sticky='w', pady=(4, 0))
        self.helix_var = tk.DoubleVar(value=20.0)
        ttk.Entry(f, textvariable=self.helix_var, width=10).grid(row=r, column=1, sticky='w', padx=(6, 0), pady=(4, 0))
        r += 1
        self.export_btn = ttk.Button(f, text='Export curves', style='Big.TButton', command=self.export_cad)
        self.export_btn.grid(row=r, column=0, columnspan=3, sticky='ew', pady=(8, 0))
        r += 1
        ttk.Label(f, style='Hint.TLabel', wraplength=self.wrap, justify='left',
                  text='Writes <part>_z0 / _z_pos / _z_neg .txt point files (mm). Import each with '
                       'Insert > Curve > Curve Through XYZ Points and loft between them. Helix hand is set '
                       'automatically so sun and planets mesh; pins and discs are exported untwisted.'
                  ).grid(row=r, column=0, columnspan=3, sticky='w', pady=(6, 0))
        r += 1
        self.export_step_btn = ttk.Button(f, text='Export STEP solids', style='Big.TButton', command=self.export_step)
        self.export_step_btn.grid(row=r, column=0, columnspan=3, sticky='ew', pady=(8, 0))
        r += 1
        ttk.Label(f, style='Hint.TLabel', wraplength=self.wrap, justify='left',
                  text='Writes <part>.step solids (mid plane at z=0) plus assembly.step with every part placed, '
                       'into the "step" subfolder. Needs CadQuery (pip install cadquery).'
                  ).grid(row=r, column=0, columnspan=3, sticky='w', pady=(6, 0))
        f.columnconfigure(1, weight=1)

        self.export_log = tk.Text(parent, height=8, wrap='word', font=('Consolas', 8), relief='flat',
                                  background=self.root.cget('background'))
        self.export_log.pack(fill='both', expand=True)
        self.export_log.configure(state='disabled')

    # ----- spec / params ----------------------------------------------------
    def _select_spec(self, name: str):
        self.stop_animation()
        self.spec = SPEC_BY_NAME[name]
        self.type_var.set(name)
        self.desc_label.configure(text=self.spec.description)

        # kinematics combos
        labels = [f'{k}  ({v})' for k, v in self.spec.members.items()]
        self._member_labels = dict(zip(self.spec.members.keys(), labels))
        self._label_members = dict(zip(labels, self.spec.members.keys()))
        for key, combo in self.kin_combos.items():
            combo.configure(values=labels)
        self._set_kin(self.spec.default_kin)
        if self.spec.has_fixed:
            self.fixed_label.grid()
            self.kin_combos['fixed'].grid()
        else:
            self.fixed_label.grid_remove()
            self.kin_combos['fixed'].grid_remove()
        if self.spec.extra_kin:
            self.extra_kin_label.configure(text=self.spec.extra_kin.label)
            self.extra_kin_label.grid(row=3, column=0, sticky='w')
            self.extra_kin_combo.configure(values=list(self.spec.extra_kin.choices))
            self.extra_kin_combo.grid(row=3, column=1, sticky='ew', padx=(6, 0), pady=1)
            self.extra_kin_var.set(self.spec.extra_kin.default)
        else:
            self.extra_kin_label.grid_remove()
            self.extra_kin_combo.grid_remove()

        # parameter widgets
        for w in self.param_widgets:
            w.destroy()
        self.param_widgets.clear()
        self.param_vars.clear()
        for i, p in enumerate(self.spec.params):
            if p.kind == 'bool':
                var = tk.BooleanVar(value=p.default)
                w = ttk.Checkbutton(self.param_frame, text=p.label, variable=var)
                w.grid(row=i, column=0, columnspan=2, sticky='w', pady=1)
                self.param_widgets.append(w)
            else:
                lbl = ttk.Label(self.param_frame, text=p.label)
                lbl.grid(row=i, column=0, sticky='w', pady=1)
                self.param_widgets.append(lbl)
                if p.kind == 'choice':
                    var = tk.StringVar(value=p.default)
                    w = ttk.Combobox(self.param_frame, textvariable=var, values=list(p.choices), state='readonly', width=12)
                elif p.kind == 'int':
                    var = tk.IntVar(value=p.default)
                    w = ttk.Spinbox(self.param_frame, textvariable=var, from_=p.minimum if p.minimum is not None else -1e9,
                                    to=1e9, width=12)
                else:
                    var = tk.DoubleVar(value=p.default)
                    w = ttk.Entry(self.param_frame, textvariable=var, width=14)
                w.grid(row=i, column=1, sticky='w', padx=(6, 0), pady=1)
                self.param_widgets.append(w)
            self.param_vars[p.key] = var

        # presets
        names = list(self.spec.presets.keys())
        self.preset_cb.configure(values=names)
        self.preset_var.set(names[0] if names else '')

        # export availability
        self.export_btn.configure(state='normal' if self.spec.export else 'disabled')
        self.play_btn.configure(state='normal' if self.spec.animatable else 'disabled')
        self.warn_label.configure(text='')

    def _set_kin(self, kin: Tuple[str, ...]):
        keys = ['input', 'output', 'fixed']
        for key, member in zip(keys, kin):
            self.kin_vars[key].set(self._member_labels.get(member, member))

    def _get_kin(self) -> Tuple[str, ...]:
        keys = ['input', 'output'] + (['fixed'] if self.spec.has_fixed else [])
        kin = []
        for key in keys:
            label = self.kin_vars[key].get()
            kin.append(self._label_members.get(label, label))
        if len(set(kin)) != len(kin):
            raise ValueError('Input, output and fixed members must all be different.')
        if self.spec.extra_kin:
            kin.append(self.extra_kin_var.get())
        return tuple(kin)

    def _get_params(self) -> Dict[str, Any]:
        out = {}
        for p in self.spec.params:
            var = self.param_vars[p.key]
            try:
                val = var.get()
            except tk.TclError:
                raise ValueError(f'"{p.label}" is not a valid {p.kind}.')
            if p.kind == 'int':
                val = int(val)
            elif p.kind == 'float':
                val = float(val)
            if p.minimum is not None and p.kind in ('int', 'float') and val < p.minimum:
                raise ValueError(f'"{p.label}" must be at least {p.minimum}.')
            out[p.key] = val
        return out

    def _on_type_change(self):
        self._select_spec(self.type_var.get())
        self._apply_preset()

    def _apply_preset(self):
        name = self.preset_var.get()
        preset = self.spec.presets.get(name)
        if not preset:
            return
        preset = dict(preset)
        kin = preset.pop('kin', None)
        if kin:
            self._set_kin(kin)
        fuse = preset.pop('fuse', None)
        if fuse:
            self.extra_kin_var.set(fuse)
        for key, val in preset.items():
            if key in self.param_vars:
                self.param_vars[key].set(val)
        self.update_gear()

    # ----- core actions -----------------------------------------------------
    def update_gear(self):
        self.stop_animation()
        try:
            kin = self._get_kin()
            params = self._get_params()
        except ValueError as e:
            self._set_status(str(e), error=True)
            return
        warnings = self.spec.validate(params)
        self.warn_label.configure(text='\n'.join(warnings))
        self._set_status('Building geometry...')
        self.root.update_idletasks()
        try:
            gear = self.spec.build(kin, params)
            # force profile generation / ratio evaluation now so errors surface here
            _ = gear.ratios_f
            self.anim_phase = 0.0
            self.phase_var.set(0.0)
            self._draw(gear, 0.0)
        except Exception as e:
            traceback.print_exc()
            self._set_status(f'Error: {e}', error=True)
            messagebox.showerror('Could not build gear train', f'{type(e).__name__}: {e}')
            return
        self.gear = gear
        self.anim_scale = self._default_anim_scale(gear)
        self._show_results(gear, kin)
        self._set_status('Ratio %s : 1   |   %s' % (self._fmt_ratio(gear.ratio_f), self.spec.name))

    def _draw(self, gear, phase: float):
        self.ax.cla()
        if self.spec.animatable:
            gear.plot(phase=phase, ax=self.ax, show=False)
            self.ax.set_aspect('equal', adjustable='box')
        else:
            gear.plot(show=False, ax=self.ax)
        title = self.ax.get_title().replace('\n', '  |  ')
        self.ax.set_title(textwrap.fill(title, 80), fontsize=8)
        self.fig.subplots_adjust(left=0.03, right=0.97, bottom=0.03, top=0.9)
        self.canvas.draw_idle()

    def _show_results(self, gear, kin):
        lines = []
        inp, out = kin[0], kin[1]
        lines.append(f'Input: {inp} ({self.spec.members.get(inp, "")})   Output: {out} ({self.spec.members.get(out, "")})')
        if self.spec.has_fixed and len(kin) > 2:
            lines.append(f'Fixed: {kin[2]} ({self.spec.members.get(kin[2], "")})')
        lines.append('')
        lines.append(f'Symbolic ratio {inp}/{out}:')
        lines.append(f'   {gear.kinematics.ratio}')
        lines.append(f'Numeric ratio: {self._fmt_ratio(gear.ratio_f)} : 1')
        lines.append('')
        lines.append('Rotation of each member per turn of the output:')
        for k, v in sorted(gear.ratios_f.items()):
            lines.append(f'   {k:>4} : {v:+.5g}')
        self._set_text(self.result_text, '\n'.join(lines))

    @staticmethod
    def _fmt_ratio(r: float) -> str:
        if abs(r) >= 100:
            return f'{r:.1f}'
        return f'{r:.4g}'

    @staticmethod
    def _default_anim_scale(gear) -> float:
        rs = [abs(1 / r) for r in gear.ratios_f.values() if r]
        if not rs:
            return 0.01
        return float(np.prod(rs) ** (1 / len(rs)) / 50)

    # ----- animation --------------------------------------------------------
    def toggle_animation(self):
        if self.anim_running:
            self.stop_animation()
        else:
            self.start_animation()

    def start_animation(self):
        if self.gear is None or not self.spec.animatable:
            return
        self.anim_running = True
        self.play_btn.configure(text='Pause  (Space)')
        self._tick()

    def stop_animation(self):
        self.anim_running = False
        if self.anim_job is not None:
            self.root.after_cancel(self.anim_job)
            self.anim_job = None
        if hasattr(self, 'play_btn'):
            self.play_btn.configure(text='Play  (Space)')

    def reset_animation(self):
        self.stop_animation()
        self.anim_phase = 0.0
        self.phase_var.set(0.0)
        if self.gear is not None:
            self._draw(self.gear, 0.0)
            self._update_phase_label()

    def step_animation(self):
        if self.gear is None:
            return
        self.anim_phase += self.anim_scale * (10 ** self.speed_var.get())
        self._sync_phase_and_draw()

    def _tick(self):
        if not self.anim_running or self.gear is None:
            return
        self.anim_phase += self.anim_scale * (10 ** self.speed_var.get())
        self._sync_phase_and_draw()
        self.anim_job = self.root.after(30, self._tick)

    def _sync_phase_and_draw(self):
        self.phase_var.set(self.anim_phase % (2 * np.pi))
        self._draw(self.gear, self.anim_phase)
        self._update_phase_label()

    def _on_phase_slider(self, _value):
        if self.gear is None or self.anim_running:
            return
        self.anim_phase = float(self.phase_var.get())
        self._draw(self.gear, self.anim_phase)
        self._update_phase_label()

    def _update_phase_label(self):
        self.phase_label.configure(text=f'{self.anim_phase % (2 * np.pi):.3f} rad   (total {self.anim_phase:.2f})')

    def _on_space(self, event):
        # ignore when typing into an entry
        if isinstance(event.widget, (tk.Entry, ttk.Entry, tk.Text, ttk.Spinbox, ttk.Combobox)):
            return
        self.toggle_animation()

    # ----- export -----------------------------------------------------------
    def save_png(self):
        if self.gear is None:
            return
        path = filedialog.asksaveasfilename(defaultextension='.png', filetypes=[('PNG image', '*.png')],
                                            initialfile=self._suggest_name('png'))
        if not path:
            return
        self.fig.savefig(path, dpi=150, bbox_inches='tight')
        self._set_status(f'Saved {path}')

    def save_gif(self):
        if self.gear is None or not self.spec.animatable:
            return
        try:
            frames = int(self.gif_frames_var.get())
            total = float(self.gif_total_var.get())
        except (tk.TclError, ValueError):
            self._set_status('GIF frames and total phase must be numbers.', error=True)
            return
        path = filedialog.asksaveasfilename(defaultextension='.gif', filetypes=[('GIF animation', '*.gif')],
                                            initialfile=self._suggest_name('gif'))
        if not path:
            return
        self.stop_animation()
        fig = Figure(figsize=(6, 6), dpi=100)
        FigureCanvasAgg(fig)
        self.gif_progress.configure(maximum=frames, value=0)

        def progress(i, n):
            self.gif_progress.configure(value=i)
            self._set_status(f'Rendering GIF frame {i}/{n}...')
            self.root.update_idletasks()

        try:
            self.gear.save_animation(frames=frames, filename=path, total=total, fig=fig, progress=progress)
        except Exception as e:
            traceback.print_exc()
            self._set_status(f'GIF export failed: {e}', error=True)
            messagebox.showerror('GIF export failed', f'{type(e).__name__}: {e}')
            return
        finally:
            self.gif_progress.configure(value=0)
        self._set_status(f'Saved {path}')

    def export_cad(self):
        if self.gear is None or self.spec.export is None:
            return
        try:
            settings = cad_export.ExportSettings(
                target_diameter_mm=float(self.diam_var.get()),
                thickness_mm=float(self.thick_var.get()),
                helix_angle_deg=float(self.helix_var.get()),
                gear_type=self.tooth_type_var.get(),
            )
        except (tk.TclError, ValueError):
            self._set_status('Export settings must be numbers.', error=True)
            return
        out_dir = self.out_dir_var.get().strip()
        if not out_dir:
            self._set_status('Choose an output folder first.', error=True)
            return
        self._set_status('Exporting curves...')
        self.root.update_idletasks()
        try:
            spec = self.spec.export(self.gear)
            report = cad_export.export_items(spec['items'], out_dir, settings,
                                             reference=spec.get('reference'), carrier_radius=spec.get('carrier_radius'))
        except Exception as e:
            traceback.print_exc()
            self._set_status(f'Export failed: {e}', error=True)
            messagebox.showerror('Export failed', f'{type(e).__name__}: {e}')
            return
        lines = [f'Exported {len(report.files)} files to {os.path.abspath(out_dir)}', '']
        lines += report.messages
        lines += ['', 'Files:'] + ['  ' + os.path.basename(f) for f in report.files]
        self._set_text(self.export_log, '\n'.join(lines))
        self._set_status(f'Exported {len(report.files)} curve files to {os.path.abspath(out_dir)}')

    def export_step(self):
        if self.gear is None or self.spec.export is None:
            return
        try:
            settings = solid_model.SolidSettings(
                target_diameter_mm=float(self.diam_var.get()),
                thickness_mm=float(self.thick_var.get()),
                helix_angle_deg=float(self.helix_var.get()),
                gear_type=self.tooth_type_var.get(),
            )
        except (tk.TclError, ValueError):
            self._set_status('Export settings must be numbers.', error=True)
            return
        out_dir = self.out_dir_var.get().strip()
        if not out_dir:
            self._set_status('Choose an output folder first.', error=True)
            return
        step_dir = os.path.join(out_dir, 'step')
        self._set_status('Building STEP solids (this can take a while)...')
        self.root.update_idletasks()
        try:
            spec = self.spec.export(self.gear)
            model = solid_model.build_solid_model(self.gear, spec, settings)
            files = step_export.write_step(model, step_dir)
        except RuntimeError as e:
            # most likely CadQuery is not installed
            self._set_status(str(e), error=True)
            messagebox.showerror('STEP export unavailable', str(e))
            return
        except Exception as e:
            traceback.print_exc()
            self._set_status(f'STEP export failed: {e}', error=True)
            messagebox.showerror('STEP export failed', f'{type(e).__name__}: {e}')
            return
        lines = [f'Exported {len(files)} STEP files to {os.path.abspath(step_dir)}', '',
                 f"{len(model['parts'])} parts, {len(model['instances'])} instances, "
                 f"{model['gear_type']}, thickness {model['thickness_mm']:g} mm, scale {model['scale_factor']:.4f}", '']
        lines += [f"  {p['name']}: half twist {p['half_twist_deg']:+.3f} deg" for p in model['parts']]
        lines += ['', 'Files:'] + ['  ' + os.path.basename(f) for f in files]
        self._set_text(self.export_log, '\n'.join(lines))
        self._set_status(f'Exported {len(files)} STEP files to {os.path.abspath(step_dir)}')

    def _browse_out_dir(self):
        d = filedialog.askdirectory(initialdir=self.out_dir_var.get() or os.getcwd())
        if d:
            self.out_dir_var.set(d)

    def _suggest_name(self, ext: str) -> str:
        base = self.spec.name.lower().split(' ')[0]
        try:
            vals = '_'.join(str(v) for p, v in ((p, self.param_vars[p.key].get()) for p in self.spec.params)
                            if p.kind == 'int')
        except tk.TclError:
            vals = ''
        return f'{base}_{vals}.{ext}' if vals else f'{base}.{ext}'

    # ----- helpers ----------------------------------------------------------
    def _set_text(self, widget: tk.Text, text: str):
        widget.configure(state='normal')
        widget.delete('1.0', 'end')
        widget.insert('1.0', text)
        widget.configure(state='disabled')

    def _set_status(self, msg: str, error: bool = False):
        self.status.set(('Error: ' if error and not msg.lower().startswith('error') else '') + msg)

    def _on_close(self):
        self.stop_animation()
        self.root.destroy()


def enable_high_dpi():
    """Render crisply on scaled Windows displays instead of being bitmap-stretched."""
    if sys.platform.startswith('win'):
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


def main():
    enable_high_dpi()
    root = tk.Tk()
    GearTrainApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
