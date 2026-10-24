"""Build the bilingual README figures. Standard library only; no external assets.

Run: python3 assets/readme/diagrams/build.py
The SVG output retains editable text and shapes, accessible titles and descriptions.
"""
from pathlib import Path
from html import escape

ROOT = Path(__file__).parent
INK = '#23343b'
MUTED = '#5c7078'
LINE = '#ccd8dc'
BLUE = '#356a85'
TEAL = '#247a71'
GOLD = '#9a703c'
BG = '#f6f8f8'


class Figure:
    def __init__(self, zh, height, number, title, sub):
        self.zh = zh
        self.parts = [f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" viewBox="0 0 1200 {height}" role="img" aria-labelledby="title desc">
<title id="title">{escape(title)}</title><desc id="desc">{escape(sub)}</desc>
<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M1 1 L8 5 L1 9" fill="none" stroke="#5c7078" stroke-width="1.6"/></marker></defs>
<style>text {{font-family:"Noto Sans CJK SC",Arial,sans-serif;fill:{INK};}} .muted{{fill:{MUTED};}} .mono{{font-family:"DejaVu Sans Mono",monospace;}}</style>
<rect width="1200" height="{height}" rx="24" fill="{BG}"/>
<path d="M40 104H1160" stroke="{LINE}"/>
''']
        self.text(40, 38, f'NERV   /   {number}', 13, BLUE, weight=700)
        self.text(40, 73, title, 28, weight=700)
        self.text(1160, 38, '架构与产品' if zh else 'PRODUCT & ARCHITECTURE', 12, MUTED, anchor='end')
        self.text(40, height-26, sub, 16, MUTED)

    def choose(self, en, zh):
        return zh if self.zh else en

    def add(self, value):
        self.parts.append(value)

    def text(self, x, y, value, size=19, color=INK, weight=400, anchor='start'):
        self.add(f'<text x="{x}" y="{y}" font-size="{size}" style="fill:{color}" font-weight="{weight}" text-anchor="{anchor}">{escape(value)}</text>')

    def lines(self, x, y, lines, size=18, color=MUTED, step=27):
        for i, value in enumerate(lines):
            self.text(x, y+i*step, value, size, color)

    def rect(self, x, y, w, h, fill='white', stroke=LINE, r=14):
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}" stroke-width="1.3"/>')

    def path(self, points, color=BLUE, dashed=False, arrow=True, both=False):
        d = 'M' + ' L'.join(f'{x},{y}' for x, y in points)
        self.add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'+(' stroke-dasharray="6 6"' if dashed else '')+(' marker-end="url(#arrow)"' if arrow else '')+(' marker-start="url(#arrow)"' if both else '')+'/>')

    def icon(self, name, x, y, color=BLUE, scale=1):
        paths = {
            'chat': '<rect x="2" y="3" width="36" height="28" rx="7"/><path d="M10 31v7l9-7M10 13h20M10 21h13"/>',
            'eye': '<path d="M1 20Q20-4 39 20Q20 44 1 20Z"/><circle cx="20" cy="20" r="7"/>',
            'policy': '<path d="M3 9l8-6h26l-8 6ZM29 9l8-6v27l-8 7Z"/><rect x="3" y="9" width="26" height="28"/><path d="M11 9v28M20 9v28M3 18h26M3 27h26"/>',
            'arm': '<path d="M5 37h30M11 36v-9l13-8-9-12M24 19l10-8M34 11l4 3M34 11l-2-5"/><circle cx="15" cy="7" r="4"/><circle cx="24" cy="19" r="4"/><circle cx="11" cy="27" r="4"/>',
            'camera': '<rect x="2" y="10" width="36" height="25" rx="5"/><path d="M10 10l4-6h12l4 6"/><circle cx="20" cy="22" r="8"/>',
            'folder': '<path d="M2 11V5h13l5 6h18v25H2Z"/><path d="M9 20h22M9 27h16"/>',
            'shield': '<path d="M20 2l16 6v13q-1 11-16 18Q5 32 4 21V8Z"/><path d="M11 20l6 6 13-14"/>',
        }
        self.add(f'<g transform="translate({x},{y}) scale({scale})" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{paths[name]}</g>')

    def box(self, x, y, w, h, title, sub=None, color=BLUE, fill='white'):
        self.rect(x, y, w, h, fill)
        self.add(f'<path d="M{x+16} {y+17}v{h-34}" stroke="{color}" stroke-width="3" stroke-linecap="round"/>')
        self.text(x+32, y+34, title, 20, weight=600)
        if sub:
            self.text(x+32, y+61, sub, 16, MUTED)

    def save(self, name):
        self.add('</svg>')
        (ROOT / ('zh' if self.zh else 'en') / f'{name}.svg').write_text('\n'.join(self.parts)+'\n')


def overview(zh):
    f = Figure(zh, 630, '01', '从任务到执行' if zh else 'From a task to robot execution',
               '使用已有策略与标定；训练在 NERV 之外完成。' if zh else 'Bring existing policies and calibration. Training stays outside NERV.')
    c=f.choose
    items=[('chat',c('One workspace','统一工作区'),c('Set up the task','设置任务'),c('Models · scenes · history','模型 · 场景 · 历史')),
           ('policy',c('Your control scheme','自选控制方案'),c('Choose how to act','选择执行方式'),c('Fixed ACT · skill group · vision','固定 ACT · 策略组 · 视觉控制')),
           ('arm',c('Real or simulation','真机或仿真'),c('Run through a Runtime','通过 Runtime 执行'),c('Separate environments and state','独立的环境与运行状态'))]
    for i,(icon,title,heading,sub) in enumerate(items):
        x=40+i*380
        f.rect(x,137,350,238)
        f.icon(icon,x+25,163, [BLUE,GOLD,TEAL][i],1.1)
        f.text(x+25,246,title,24,weight=600)
        f.text(x+25,285,heading,19)
        f.text(x+25,321,sub,16,MUTED)
        f.text(x+309,175,f'0{i+1}',15,MUTED)
        if i<2: f.path([(x+351,255),(x+378,255)],color=MUTED)
    f.rect(40,405,1120,145,'#eaf1f2')
    f.text(65,441,c('Observe, control and keep a record','观测、控制与记录'),20,weight=600)
    for x,icon,title,sub in [(65,'camera',c('Shared observations','共享观测'),c('top / wrist + joint state','top / wrist 与关节状态')),
                            (440,'shield',c('Operator control','操作者控制'),c('Enable · stop · release','启用 · 停止 · 卸力')),
                            (815,'folder',c('Local history','本地历史'),c('Tasks and execution feedback','任务与执行反馈'))]:
        f.icon(icon,x,469,TEAL,.8)
        f.text(x+47,483,title,18,weight=600)
        f.text(x+47,510,sub,16,MUTED)
    f.save('overview')


def architecture(zh):
    f=Figure(zh,840,'02','三层职责，共用运行基础' if zh else 'Three layers, one managed execution path',
             '层表示职责，不要求三个模型；固定策略跳过决策层。' if zh else 'Layers define responsibilities. Fixed policies bypass the decision model.')
    c=f.choose
    cards=[(40,BLUE,'01',c('Interaction','交互层'),c('UI + NERV Agent','界面与 NERV Agent'),'chat',
            [c('Receive goals and explain state','接收目标，解释状态'),c('Use the selected chat model','使用选定的聊天模型'),c('Submit to the chosen scheme','向选定控制方案提交任务')]),
           (425,GOLD,'02',c('Decision · optional','决策层 · 可选'),c('System 2','System 2'),'eye',
            [c('Observe and choose the next step','观测并选择下一步'),c('Use only permitted skills/tools','仅调用允许的技能或工具'),c('Check progress and completion','检查进展与结束条件')]),
           (810,TEAL,'03',c('Execution','执行层'),c('System 1','System 1'),'policy',
            [c('ACT or bounded local control','ACT 或本地有限动作控制'),c('Infer, interpolate and track','推理、插值与跟踪'),c('Return execution feedback','返回执行反馈')])]
    for x,color,num,title,sub,icon,lines in cards:
        f.rect(x,142,350,335)
        f.text(x+24,178,num,15,color,700)
        f.text(x+24,217,title,24,weight=600)
        f.text(x+24,247,sub,17,color)
        f.icon(icon,x+280,166,color)
        f.add(f'<path d="M{x+24} 272H{x+326}" stroke="{LINE}"/>')
        f.lines(x+24,309,lines,18,step=41)
    f.path([(390,335),(421,335)],GOLD)
    f.path([(775,335),(806,335)],TEAL)
    f.text(600,514,c('USER-SELECTED SCHEME · LOCKED FOR EACH TASK','用户选定控制方案 · 启动任务时锁定'),16,MUTED,600,'middle')
    f.path([(215,478),(215,554),(985,554),(985,478)],BLUE)
    f.rect(420,536,360,35,BG,BG,8)
    f.text(600,560,c('Fixed ACT: no decision model','固定 ACT：不创建决策模型'),18,BLUE,600,'middle')
    f.rect(40,600,1120,152,'#eaf1f2')
    f.icon('shield',66,623,TEAL)
    f.text(120,647,c('Shared foundation · Host + Runtime','共用运行基础 · Host 与 Runtime'),22,weight=600)
    f.text(66,688,c('Control grants   /   Action validation   /   Scheduling   /   Stop   /   Event journal','控制授权   /   动作校验   /   调度   /   停止   /   事件记录'),20)
    f.text(66,723,c('Runtime supplies timestamped observations and measured feedback to the UI and controllers.','Runtime 向界面与控制器提供带时间信息的观测和实测反馈。'),17,MUTED)
    for x in (215,600,985): f.path([(x,569),(x,597)],MUTED,True)
    f.save('architecture')


def schemes(zh):
    f=Figure(zh,800,'03','三种方案，由用户选择' if zh else 'Three control schemes, chosen by the user',
             '所有路线共用控制授权与停止机制；动作结束不等于任务成功。' if zh else 'All routes share authorization and stop controls. Execution ending is not task success.')
    c=f.choose
    rows=[(140,BLUE,c('Fixed policy','固定策略'),c('No decision model','不启用决策模型'),
           [c('Run in NERV','在 NERV 中运行'),c('Selected ACT','选定的 ACT'),c('Runtime + device','Runtime 与设备')],
           c('Run button needs no API key. Starting through chat still needs a chat model.','运行按钮无需 API key；通过对话发起时需要聊天模型。')),
          (340,GOLD,c('Policy group','策略组执行'),c('Visual supervision','视觉模型监督'),
           [c('Visual decision','视觉模型决策'),c('Allowed ACT skills','允许的 ACT 技能'),c('Runtime + device','Runtime 与设备')],
           c('One skill runs at a time. Skill transitions need verified starting conditions.','每次只运行一个技能；技能衔接需要经过验证的起始条件。')),
          (540,TEAL,c('Visual direct','视觉直接控制'),c('No ACT required','无需 ACT'),
           [c('Visual decision','视觉模型决策'),c('Bounded tools','有限动作工具'),c('Runtime + device','Runtime 与设备')],
           c('Joint moves and gripper commands execute locally; observe again after execution.','关节移动与夹爪指令在本地执行，结束后重新观测。'))]
    for y,color,title,sub,boxes,note in rows:
        f.rect(40,y,1120,180)
        f.text(64,y+41,title,23,color,600)
        f.text(64,y+71,sub,16,MUTED)
        for i,label in enumerate(boxes):
            x=310+i*280
            f.rect(x,y+22,244,60,'#f4f7f7')
            f.text(x+122,y+59,label,19,INK,600,'middle')
            if i<2:f.path([(x+246,y+52),(x+276,y+52)],color)
        if y == 140:
            f.path([(991,y+83),(991,y+108),(712,y+108),(712,y+83)],MUTED,True)
            f.rect(750,y+94,200,28,'white','white',4)
            f.text(850,y+114,c('Policy observations','策略观测'),15,MUTED,anchor='middle')
        if y>140:
            f.path([(991,y+83),(991,y+108),(432,y+108),(432,y+83)],MUTED,True)
            f.rect(578,y+94,280,28,'white','white',4)
            f.text(718,y+114,c('Observations + feedback','观测与执行反馈'),15,MUTED,anchor='middle')
        f.text(64,y+155,note,17,MUTED)
    f.save('control-schemes')


def runtime(zh):
    f=Figure(zh,900,'04','标准 SO-101：进程与设备边界' if zh else 'Standard SO-101: process and device boundaries',
             '此图描述真机接入；仿真使用独立 Runtime 绑定，不复用真机标定。' if zh else 'Real-device route shown. Simulation uses a separate Runtime binding and no real calibration.')
    c=f.choose
    f.rect(40,145,500,440)
    f.text(64,182,c('NERV host environment','NERV 主程序环境'),24,BLUE,600)
    f.text(64,212,c('Browser UI connects to the local host','浏览器界面连接本机主进程'),17,MUTED)
    f.box(64,243,452,90,c('Interaction + optional decision','交互与可选决策'),c('Independent model contexts','独立的模型上下文'))
    f.box(64,357,452,90,c('Tasks + control grants','任务与控制授权'),c('Frozen scheme, instance and action limits','锁定方案、实例与动作约束'))
    f.box(64,471,452,90,c('Local configuration + history','本地配置与历史'),c('Models · schemes · sessions · event journal','模型 · 方案 · 会话 · 事件记录'))
    f.rect(660,145,500,440)
    f.text(684,182,c('SO-101 Runtime environment','SO-101 Runtime 环境'),24,TEAL,600)
    f.text(684,212,c('Independent worker · LeRobot 0.6.1','独立 worker · LeRobot 0.6.1'),17,MUTED)
    f.box(684,243,452,90,c('ACT plugin / bounded tools','ACT 插件 / 有限动作工具'),c('Inference and local action generation','策略推理与本地动作生成'),TEAL)
    f.box(684,357,452,90,c('Shared scheduler + validation','共用调度器与动作校验'),c('Validate every candidate target','校验每个候选动作目标'),TEAL)
    f.box(684,471,452,90,c('One native LeRobot instance','唯一的原生 LeRobot 实例'),'robot.send_action() / observations',TEAL)
    f.path([(910,333),(910,353)],TEAL)
    f.path([(910,447),(910,467)],TEAL)
    f.path([(517,385),(680,385)],BLUE)
    f.text(600,364,c('Runtime SDK','Runtime SDK'),16,BLUE,600,'middle')
    f.path([(681,421),(519,421)],TEAL)
    f.text(600,451,c('Feedback','执行反馈'),16,TEAL,anchor='middle')
    f.rect(40,625,500,185,'#eaf1f2')
    f.text(64,662,c('Keep three records distinct','分别保留三项记录'),22,weight=600)
    f.lines(64,701,[c('01  Requested target','01  控制器请求的目标'),c('02  Target actually sent by LeRobot','02  LeRobot 实际下发的目标'),c('03  Measured position afterward','03  后续实测位置')],18,step=32)
    f.path([(910,561),(910,606),(776,606),(776,625)],TEAL,both=True)
    f.path([(1058,625),(1058,606),(940,606),(940,562)],TEAL)
    for x,icon,title,sub in [(660,'arm',c('SO-101 follower','SO-101 从臂'),c('Six position channels','六个位置通道')),
                            (920,'camera',c('top + wrist','top + wrist'),c('One shared acquisition','共享采集结果'))]:
        f.rect(x,625,240,185)
        f.icon(icon,x+22,648,TEAL)
        f.text(x+22,724,title,21,weight=600)
        f.text(x+22,759,sub,17,MUTED)
    f.save('runtime')


if __name__=='__main__':
    for zh in (False,True):
        for draw in (overview,architecture,schemes,runtime):
            draw(zh)
