"""Four independently composed NERV 0.9 figures. Native editable SVG; stdlib only."""
from build import Figure, BLUE, TEAL, GOLD, MUTED, LINE


def note(f, kind, idea, geometry):
    f.add(f'<!-- SVG-DESIGN-NOTES Type: {kind}. Q0: {idea}. Q1: {geometry}. Q2: Explicit robot execution and authority boundaries. Q3: No decorative pictograms. Q4: Labels adjacent to their paths. Q5: Shared bilingual geometry; opaque light background for GitHub themes. -->')


def platform(zh):
    f = Figure(zh, 880, '0.9 / 01', 'Agent 与机器人之间的运行基础' if zh else 'A robot platform for external Agents',
        '0.9.0 本地候选版 · 操作者授权与本地停止独立于模型。' if zh else '0.9.0 local candidate · Operator authority and local stopping remain independent of the model.')
    c = f.choose
    note(f, 'A', 'External decisions pass through a local authority boundary.', 'Nested Host and Runtime regions separate decision ownership from execution.')
    f.box(40,140,350,112,c('External Agent','外部 Agent'),c('User dialogue · model context','用户对话 · 模型上下文'),BLUE)
    f.box(810,140,350,112,c('Operator workbench','操作者工作台'),c('World · cameras · activity','世界 · 相机 · 活动'),TEAL)
    f.text(410,179,c('Same-machine MCP','同机 MCP'),18,BLUE)
    f.path([(390,208),(600,208),(600,302)],both=True)
    f.text(950,286,c('Explicit motion authorization','显式动作授权'),17,TEAL,anchor='end')
    f.path([(985,252),(985,302)],TEAL,both=True)
    f.rect(40,302,1120,348,'#edf2f3')
    f.text(65,338,'NERV HOST',15,BLUE,700)
    f.box(65,360,1070,88,c('Project · Run · Task','Project · Run · Task'),c('Identity · permissions · budgets · events · supervision expiry','身份 · 权限 · 预算 · 事件 · 监督期限'),BLUE)
    f.box(65,501,420,100,c('External direct / skills','外部直接决策／技能调用'),c('Bounded action requests','受限动作请求'),BLUE)
    f.box(640,501,495,100,c('Optional decision service','可选机器人决策服务'),c('Visual supervision · allowed skills','视觉监督 · 允许的技能'),GOLD)
    f.path([(275,448),(275,501)])
    f.path([(887,448),(887,501)],GOLD)
    f.path([(275,601),(275,639),(600,639),(600,674)],TEAL)
    f.path([(887,601),(887,639),(600,639)],TEAL,arrow=False)
    f.box(65,674,1070,72,c('Execution: ACT · motion policy · bounded controller','执行：ACT · 运动策略 · 有限动作控制器'),color=TEAL)
    f.path([(600,746),(600,785)],TEAL,both=True)
    f.text(600,816,c('Runtime owns the real device or simulated robot and its sensors','Runtime 持有真实设备或仿真机器人及其传感器'),20,TEAL,anchor='middle')
    f.save('platform-090')


def supervision(zh):
    f = Figure(zh, 840, '0.9 / 02', '监督依赖观测确认，不依赖连接心跳' if zh else 'Supervision requires observation acknowledgement',
        '时序示意；不表示真实延迟。' if zh else 'Sequence illustration; spacing does not represent measured latency.')
    c = f.choose
    note(f,'C','A live transport is not evidence of model supervision.','Three lifelines distinguish delivery, acknowledgement, execution and local timeout.')
    for x,title in [(200,c('External Agent','外部 Agent')),(600,'NERV Host'),(1000,'Runtime')]:
        f.box(x-145,140,290,68,title,color=BLUE if x<1000 else TEAL)
        f.path([(x,225),(x,724)],LINE,dashed=True,arrow=False)
    def msg(y,a,b,en,cn,color=BLUE):
        f.text((a+b)/2,y-19,c(en,cn),17,color,anchor='middle'); f.path([(a,y),(b,y)],color)
    msg(268,200,600,'Request observation','请求观测')
    msg(333,1000,600,'Frames + identity + timestamp','帧 + 实例身份 + 采集时间',TEAL)
    msg(398,600,200,'Deliver images + receipt','交付图像与回执')
    msg(463,200,600,'Acknowledge this receipt','确认本次观测回执')
    msg(528,200,600,'Task / bounded action request','任务／有限动作请求')
    msg(593,600,1000,'Validate authority; execute','核对授权后执行',TEAL)
    msg(658,1000,600,'Execution status and result','执行状态与结果',TEAL)
    f.rect(40,735,1120,53,'#f3eadc')
    f.text(600,769,c('If supervision expires: Host requests a local stop; report whether it was confirmed.','监督过期：Host 请求本地停止，并报告停止是否已确认。'),18,GOLD,anchor='middle')
    f.save('supervision-090')


def lifecycle(zh):
    f = Figure(zh, 790, '0.9 / 03', '项目保存配置；运行保存实际发生的事' if zh else 'Projects persist; runs record what actually happened',
        '重新打开不恢复动作授权；新修订不改变运行中任务。' if zh else 'Reopening never restores motion authority. New revisions do not change running tasks.')
    c = f.choose
    note(f,'A','Configuration lifetime is longer than runtime and task lifetime.','Nested regions and a second inactive Run distinguish historical instances from current execution.')
    f.rect(40,140,1120,560,'#edf2f3')
    f.text(70,184,'PROJECT',22,BLUE,700)
    f.text(70,220,c('Robot · world · execution structure · layout · revision','机器人 · 世界 · 执行结构 · 布局 · 修订'),19,MUTED)
    f.rect(70,259,735,401,'white')
    f.text(95,300,c('Current RUN','当前 RUN'),22,TEAL,700)
    f.text(95,336,c('One instance identity; at most one active run per project','独立实例身份；每个项目至多一个活动运行'),17,MUTED)
    f.rect(100,373,675,250,'#f6f8f8')
    f.text(125,411,'TASK',21,BLUE,700)
    f.text(125,447,c('Goal · frozen revision · budget · control generation','目标 · 锁定修订 · 预算 · 控制权代次'),18,MUTED)
    f.box(125,485,280,98,'EXECUTION 01',c('Policy or bounded action','策略或有限动作'),TEAL)
    f.box(465,485,280,98,'EXECUTION 02',c('Separate outcome record','独立执行结果'),TEAL)
    f.path([(405,534),(465,534)],TEAL)
    f.rect(845,259,285,401,'#f3f4f4')
    f.text(870,302,c('Past RUN','历史 RUN'),22,MUTED,700)
    f.lines(870,352,[c('Ended / interrupted','已结束／已中断'),c('History remains readable','保留历史记录'),c('No command replay','不重放命令')],17,step=34)
    f.text(600,739,c('Weights and official assets are immutable references, not copied into every project.','权重与官方资产使用不可变引用，不为每个项目复制一份。'),18,MUTED,anchor='middle')
    f.save('lifecycle-090')


def deployments(zh):
    f = Figure(zh, 870, '0.9 / 04', '相同执行接口，不同部署位置' if zh else 'One execution interface, different deployment locations',
        '虚线区域为后续规划；本版不提供远程接入或独立 Brain。' if zh else 'Dashed regions are future work. Remote access and an independent Brain are not delivered in 0.9.0.')
    c=f.choose
    note(f,'A','Edge Brain and remote supervision reuse MCP without replacing Host tasks.','Solid local lane and dashed future lanes distinguish availability from deployment intent.')
    lanes=[(140,c('0.9.0 candidate · one computer','0.9.0 候选版 · 同一台电脑'),False),(365,c('Future · edge device','未来 · 边缘设备'),True),(590,c('Future · cloud + edge','未来 · 云端 + 边缘'),True)]
    for y,title,future in lanes:
        f.add(f'<rect x="40" y="{y}" width="1120" height="190" rx="16" fill="white" stroke="{LINE}" stroke-width="1.5"'+(' stroke-dasharray="7 7"' if future else '')+'/>')
        f.text(65,y+36,title,21,GOLD if future else BLUE,600)
    f.box(70,210,350,88,c('Your Agent client','用户的 Agent 客户端'),c('Model and conversation','模型与对话'),BLUE)
    f.box(750,210,380,88,c('NERV + local Runtime','NERV + 本地 Runtime'),c('Robot / simulation','机器人／仿真'),TEAL)
    f.text(585,237,'MCP / stdio',19,BLUE,anchor='middle');f.path([(420,266),(750,266)],both=True)
    f.box(70,435,350,88,c('Independent Brain','独立 Brain'),c('API or local model','模型 API 或本地模型'),GOLD)
    f.box(750,435,380,88,c('NERV + local Runtime','NERV + 本地 Runtime'),c('Same task and stop services','复用任务与停止服务'),TEAL)
    f.text(585,462,'MCP',19,GOLD,anchor='middle');f.path([(420,491),(750,491)],GOLD,dashed=True,both=True)
    f.box(70,660,350,88,c('Cloud Agent','云端 Agent'),c('Remote supervision','远程监督'),GOLD)
    f.box(750,660,380,88,c('Edge NERV + Runtime','边缘 NERV + Runtime'),c('Local stopping survives link loss','断网时保留本地停止'),TEAL)
    f.text(585,687,c('Authenticated transport','经认证的传输'),17,GOLD,anchor='middle');f.path([(420,716),(750,716)],GOLD,dashed=True,both=True)
    f.save('deployments-090')

if __name__=='__main__':
    for language in (False,True):
        platform(language)
        supervision(language)
        lifecycle(language)
        deployments(language)
