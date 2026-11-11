"""Editable bilingual Brain Core figures; architecture contract, not acceptance evidence."""
from build import Figure, BLUE, TEAL, GOLD, MUTED


def base(zh, number, title, cn, height=800):
    return Figure(zh, height, '0.9.1 / '+number, cn if zh else title,
        '架构契约 · 独立会合验收待完成' if zh else 'Architecture contract · Independent rendezvous acceptance pending')


def platform(zh):
    f=base(zh,'01','External Agents, one NERV, one physical world','外部 Agent、同一个 NERV、同一个物理世界',840)
    c=f.choose
    for x,letter in [(60,'A'),(650,'B')]:
        f.box(x,140,490,80,c('External Agent ','外部 Agent ')+letter,c('Reasoning · vision · model context','理解 · 视觉判断 · 模型上下文'))
        f.path([(x+245,220),(x+245,300)])
        f.text(x+265,270,'MCP '+letter,18,BLUE)
    f.rect(40,300,1120,485,'#edf2f3')
    f.text(65,336,'NERV / BRAIN CORE',23,BLUE,700)
    f.text(1100,336,c('Operator console → bindings and authority','操作者控制台 → 绑定与授权'),19,TEAL,anchor='end')
    f.text(65,370,c('Identity · observations · tasks · authority · receipts','身份 · 观测 · 任务 · 授权 · 回执'),21)
    for x,letter in [(65,'A'),(655,'B')]:
        f.box(x,400,480,82,'Brain '+letter+' / Robot '+letter,c('Persistent identity · separate Run and lease','持久身份 · Run 与租约分别管理'))
        f.path([(x+240,482),(x+240,525)],TEAL)
        f.box(x,525,480,80,'Runtime '+letter+' / G1 '+letter,c('Policy · control · local stop','策略 · 控制 · 本地停止'),TEAL)
        f.path([(x+240,605),(x+240,680)],TEAL,both=True)
    f.box(65,680,1070,82,c('Shared World instance','共享 World instance'),c('One clock · one physics model · collisions between robots','一个时钟 · 一个物理模型 · 机器人间真实碰撞'),TEAL)
    f.save('platform-091')


def lifecycle(zh):
    f=base(zh,'02','World and robot identity outlive connections and Runs','World 与机器人身份跨连接、跨 Run 存在',900)
    c=f.choose
    f.box(60,140,1080,86,'World',c('Zero or more robots · one clock · versioned topology','允许零台机器人 · 同一时钟 · 拓扑修订'),TEAL)
    for x,letter in [(60,'A'),(650,'B')]:
        f.path([(x+245,226),(x+245,275)],TEAL)
        f.box(x,275,490,86,'Robot '+letter+' / Brain '+letter,c('Persistent identity and Project history','持久身份与 Project 历史'))
        f.path([(x+245,361),(x+245,410)])
        f.box(x,410,490,86,c('Runtime attachment','Runtime 执行连接'),c('Own sensors · policy · supervised balance','独立传感器 · 策略 · 受监督保持'),TEAL)
        f.path([(x+245,496),(x+245,545)])
        f.box(x,545,490,86,c('Agent / lease / Run','Agent / 租约 / Run'),c('Observers may coexist; one motion owner','可并存观察者；仅一个运动控制者'),GOLD)
    f.rect(60,675,1080,155,'#edf2f3')
    f.lines(85,709,[c('End Run → revoke motion; retain robot and Brain.','结束 Run → 撤销运动；保留 Robot 与 Brain。'),
        c('Membership change → stop, compile, migrate, commit, observe again.','增删成员 → 停止、编译、迁移、提交、重新观测。'),
        c('Host restart → recover identities; operator restarts World; no motion replay.','Host 重启 → 恢复身份；操作者重启 World；不重放运动。')],19,step=34)
    f.save('lifecycle-091')


def supervision(zh):
    f=base(zh,'03','Evidence and authority remain separate','观测、授权与实际执行分别核对',850)
    c=f.choose
    for x,title in [(190,c('External Agent','外部 Agent')),(600,'Brain Core'),(1010,'Runtime')]:
        f.box(x-140,140,280,70,title)
        f.path([(x,225),(x,730)],MUTED,dashed=True,arrow=False)
    messages=[(275,1010,190,'Sensor frames + identity + sequence','传感器图像 + 身份 + 序号'),(365,190,600,'Acknowledge inspected observation','确认已检查的观测'),(455,190,600,'Bounded action + task identity','有限动作 + 任务身份'),(545,600,1010,'Check lease, topology and action deadline','核对租约、拓扑与动作期限'),(635,1010,190,'Measured outcome + current stop evidence','实测结果 + 当前停止证据')]
    for y,a,b,en,cn in messages:
        f.rect(min(a,b)+8,y-45,abs(b-a)-16,32,'#ffffff')
        f.text((a+b)/2,y-20,c(en,cn),18,BLUE,anchor='middle')
        f.path([(a,y),(b,y)],TEAL)
    f.text(600,770,c('Agent statement ≠ execution completion ≠ independently scored task success','Agent 自述 ≠ 执行完成 ≠ 独立评分成功'),21,GOLD,anchor='middle')
    f.save('supervision-091')


def deployments(zh):
    f=base(zh,'04','Brain Core stays inside NERV','Brain Core 始终属于 NERV',720)
    c=f.choose
    f.text(60,155,c('LOCAL CONNECTION','本地连接'),19,BLUE,700)
    f.box(60,180,330,105,c('External Agent','外部 Agent'),c('Independent model context','独立模型上下文'))
    f.box(465,180,675,105,'NERV',c('Brain Core → Runtime → robot','Brain Core → Runtime → 机器人'),TEAL)
    f.path([(390,232),(465,232)],BLUE,both=True)
    f.text(60,370,c('FUTURE DEPLOYMENT — NOT DELIVERED IN 0.9.1','未来部署方式 — 本版不交付'),19,GOLD,700)
    f.rect(40,400,1120,210,'#f3eadc')
    f.box(60,430,330,100,c('Local Agent project','独立本地 Agent 项目'),c('Models · reasoning · context','模型 · 推理 · 上下文'),GOLD)
    f.box(465,430,675,100,'NERV',c('Brain Core → Runtime → local robot','Brain Core → Runtime → 本地机器人'),TEAL)
    f.path([(390,480),(465,480)],GOLD,dashed=True,both=True)
    f.text(600,578,c('Remote cloud access needs a separate authenticated transport contract.','远程云接入需要另行建立认证传输契约。'),18,MUTED,anchor='middle')
    f.save('deployments-091')


if __name__=='__main__':
    for language in [False,True]:
        for draw in [platform,lifecycle,supervision,deployments]:
            draw(language)
