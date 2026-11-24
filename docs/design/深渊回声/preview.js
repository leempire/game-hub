'use strict';

const weapons = [
  { id:'rivet', name:'铆钉发射器', role:'近敌点射', color:'#f4c27f', damage:12, cooldown:.6, reach:270 },
  { id:'arc', name:'裂潮电弧', role:'最多连锁四只', color:'#b1adff', damage:14, cooldown:1.1, reach:200 },
  { id:'ring', name:'声呐震环', role:'自身周围击退', color:'#72f2d9', damage:17, cooldown:1.35, reach:105 },
  { id:'spore', name:'荧孢罐', role:'持续伤害云团', color:'#d6e68d', damage:4, cooldown:1.45, reach:210 },
  { id:'charge', name:'深压雷', role:'延迟范围爆破', color:'#ff9380', damage:35, cooldown:1.8, reach:240 },
  { id:'harpoon', name:'贯流鱼叉', role:'直线贯穿', color:'#87ddff', damage:24, cooldown:1.25, reach:360 },
];

const $ = id => document.getElementById(id);
const canvas = $('battle');
const g = canvas.getContext('2d');
const world = { w:1440, h:1000 };
const duration = 75;
const floor = AbyssArt.floor(world.w, world.h);
const keys = new Set();
const directions = { w:[0,-1], a:[-1,0], s:[0,1], d:[1,0], arrowup:[0,-1], arrowleft:[-1,0], arrowdown:[0,1], arrowright:[1,0] };
let pointer = null;
let selected = weapons[0];
let view = { w:0, h:0, dpr:1 };
let game;

function reset(running) {
  pointer = null;
  keys.clear();
  game = {
    running, time:0, hp:100, level:1, xp:0, kills:0, invulnerable:0, cooldown:0, spawnIn:.9,
    enemies:[], loot:[], effects:[], fields:[], boss:false,
    player:{ x:world.w/2, y:world.h/2, angle:-.7 },
  };
  for (let i=0; i<8; i++) {
    const angle = i * Math.PI / 4;
    spawn(['drifter','crab','eel'][i % 3], game.player.x + Math.cos(angle)*(140+i*10), game.player.y + Math.sin(angle)*(110+i*7));
  }
  for (let i=0; i<5; i++) game.loot.push({x:game.player.x-85+i*22, y:game.player.y+64+i*3, xp:1});
  $('status').hidden = running;
  $('restart').textContent = running ? '重新试潜' : '开始试潜';
  updateHud();
}
function spawn(kind, x, y) {
  const stats = { drifter:[18,39,11], eel:[16,80,9], crab:[32,26,15], warden:[220,38,23] };
  const [hp, speed, radius] = stats[kind];
  game.enemies.push({ x, y, kind, hp, maxHp:hp, speed, radius, angle:0, flash:0 });
}
const distance = (a,b) => Math.hypot(a.x-b.x,a.y-b.y);
const clamp = (value,min,max) => Math.max(min,Math.min(max,value));

function finish(won) {
  game.running = false;
  pointer = null;
  $('restart').textContent = '再次试潜';
  const status = $('status');
  status.firstElementChild.textContent = won ? '回收完成' : '潜水服失压';
  status.lastElementChild.textContent = `等级 ${game.level} · 击退 ${game.kills} 只生物`;
  status.hidden = false;
}
function updateHud() {
  const remaining = Math.ceil(duration-game.time);
  const needed = 10 + 4*(game.level-1);
  $('time').textContent = `0${Math.floor(remaining/60)}:${String(remaining%60).padStart(2,'0')}`;
  $('health').textContent = `${game.hp} / 100`;
  $('hp').value = game.hp;
  $('level').textContent = `等级 ${game.level}`;
  $('xp').max = needed;
  $('xp').value = game.xp;
  $('xpText').textContent = `${game.xp} / ${needed} XP`;
  $('sector').textContent = game.boss ? '断缆守卫来袭' : '沉没回收站';
}
function damage(enemy, amount) {
  enemy.hp -= amount;
  enemy.flash = .13;
}
function attack() {
  const p = game.player;
  const candidates = game.enemies.filter(e => e.hp>0).sort((a,b) => distance(p,a)-distance(p,b));
  const target = candidates[0];
  const w = selected;
  if (!target || distance(target,p)>w.reach) return;
  game.cooldown = w.cooldown;
  const power = w.damage + (w.id === 'spore' ? Math.floor((game.level-1)/2) : 2*(game.level-1));
  const point = actor => [actor.x,actor.y];
  const beam = points => game.effects.push({ points, color:w.color, life:.2, max:.2 });
  const ring = (x,y,r) => game.effects.push({ x,y,r,color:w.color,life:.36,max:.36 });
  switch (w.id) {
    case 'ring':
      for (const enemy of candidates.filter(e=>distance(p,e)<=w.reach)) {
        damage(enemy,power);
        const angle = Math.atan2(enemy.y-p.y,enemy.x-p.x);
        enemy.x += Math.cos(angle)*20;
        enemy.y += Math.sin(angle)*20;
      }
      ring(p.x,p.y,w.reach);
      break;
    case 'arc': {
      const hit = [target];
      for (let i=0;i<3;i++) {
        const previous = hit.at(-1);
        const next = candidates.filter(e=>!hit.includes(e) && distance(previous,e)<105)
          .sort((a,b)=>distance(previous,a)-distance(previous,b))[0];
        if (!next) break;
        hit.push(next);
      }
      hit.forEach(e=>damage(e,power));
      beam([point(p),...hit.map(point)]);
      break;
    }
    case 'spore':
    case 'charge':
      game.fields.push({ x:target.x,y:target.y,weapon:w,power,timer:w.id==='spore'?.12:.65,pulses:w.id==='spore'?6:1,r:w.id==='spore'?60:84 });
      beam([point(p),point(target)]);
      break;
    case 'harpoon': {
      const angle = Math.atan2(target.y-p.y,target.x-p.x);
      const dx = Math.cos(angle), dy = Math.sin(angle);
      for (const e of candidates) {
        const x=e.x-p.x,y=e.y-p.y;
        const forward=x*dx+y*dy;
        if (forward>0 && forward<=w.reach && Math.abs(x*dy-y*dx)<e.radius+6) damage(e,power);
      }
      beam([point(p),[p.x+dx*w.reach,p.y+dy*w.reach]]);
      break;
    }
    default:
      damage(target,power);
      beam([point(p),point(target)]);
  }
}

function simulate(dt) {
  const p=game.player;
  game.time=Math.min(duration,game.time+dt);
  game.invulnerable=Math.max(0,game.invulnerable-dt);
  game.cooldown=Math.max(0,game.cooldown-dt);
  game.spawnIn-=dt;
  let dx=pointer?.dx ?? 0, dy=pointer?.dy ?? 0;
  for (const key of keys) {
    dx+=directions[key][0];
    dy+=directions[key][1];
  }
  const length=Math.max(1,Math.hypot(dx,dy));
  if (dx || dy) {
    p.angle=Math.atan2(dy,dx);
    p.x=clamp(p.x+dx/length*158*dt,23,world.w-23);
    p.y=clamp(p.y+dy/length*158*dt,23,world.h-23);
  }
  if (game.spawnIn<=0 && game.enemies.length<80) {
    const angle=Math.random()*Math.PI*2;
    const spread=180+Math.random()*115;
    const kind=game.time<14?'drifter':Math.random()<.5?'drifter':Math.random()<.5?'crab':'eel';
    spawn(kind,clamp(p.x+Math.cos(angle)*spread,18,world.w-18),clamp(p.y+Math.sin(angle)*spread,18,world.h-18));
    game.spawnIn=Math.max(.38,1.12-game.time/88);
  }
  if (game.time>=55 && !game.boss) {
    game.boss=true;
    const x=p.x>world.w/2?p.x-220:p.x+220;
    spawn('warden',x,p.y);
  }
  for (const enemy of game.enemies) {
    enemy.flash=Math.max(0,enemy.flash-dt);
    const gap=distance(p,enemy);
    enemy.angle=Math.atan2(p.y-enemy.y,p.x-enemy.x);
    const step=Math.min(gap,enemy.speed*dt);
    enemy.x+=Math.cos(enemy.angle)*step;
    enemy.y+=Math.sin(enemy.angle)*step;
  }
  if (!game.cooldown) attack();
  for (const field of game.fields) {
    field.timer-=dt;
    if (field.timer>0) continue;
    for (const e of game.enemies) if (e.hp>0 && distance(e,field)<=field.r) damage(e,field.power);
    field.pulses--;
    field.timer+=.25;
    if (field.weapon.id==='charge') game.effects.push({...field,color:field.weapon.color,life:.36,max:.36});
  }
  game.fields=game.fields.filter(field=>field.pulses>0);
  game.enemies=game.enemies.filter(e=>{
    if (e.hp>0) return true;
    game.kills++;
    game.loot.push({x:e.x,y:e.y,xp:e.kind==='warden'?12:e.kind==='crab'?2:1});
    return false;
  });
  const hit=game.enemies.find(e=>distance(e,p)<e.radius+10);
  if (!game.invulnerable && hit) {
    game.hp=Math.max(0,game.hp-(hit.kind==='warden'?20:10));
    game.invulnerable=.8;
  }
  game.loot=game.loot.filter(item=>{
    const gap=distance(p,item);
    if (gap<=58 && gap>15) {
      const step=Math.min(225*dt,gap);
      item.x+=(p.x-item.x)/gap*step;
      item.y+=(p.y-item.y)/gap*step;
    }
    if (distance(p,item)>15) return true;
    game.xp+=item.xp;
    return false;
  });
  while (game.xp>=10+4*(game.level-1)) {
    game.xp-=10+4*(game.level-1);
    game.level++;
    game.effects.push({x:p.x,y:p.y,r:65,color:'#c2ffbb',life:.65,max:.65});
  }
  for (const effect of game.effects) effect.life-=dt;
  game.effects=game.effects.filter(effect=>effect.life>0);
  if (!game.hp) finish(false);
  else if (game.time>=duration) finish(true);
}

function render(now) {
  const {w,h,dpr}=view;
  if (!w || !h) return;
  const p=game.player;
  const left=clamp(p.x-w/2,0,Math.max(0,world.w-w));
  const top=clamp(p.y-h/2,0,Math.max(0,world.h-h));
  g.setTransform(dpr,0,0,dpr,0,0);
  g.save();
  g.translate(-left,-top);
  g.drawImage(floor,0,0);
  const light=g.createRadialGradient(p.x,p.y,18,p.x,p.y,Math.max(295,w*.63));
  light.addColorStop(0,'#89decb1e');
  light.addColorStop(.44,'#0c242a08');
  light.addColorStop(1,'#031017b3');
  g.fillStyle=light;
  g.fillRect(left,top,w,h);

  for (let i=0;i<42;i++) {
    const x=left+((i*149.3+now*(5+i%4))%w);
    const y=top+((i*71.7+now*3)%h);
    AbyssArt.ellipse(g,x,y,i%3===0?1.5:.75,.9,'#91e8d144');
  }
  for (const field of game.fields) {
    const spore=field.weapon.id==='spore';
    AbyssArt.ellipse(g,field.x,field.y,field.r,field.r,spore?'#b7ce5c29':'#e4887526',field.weapon.color);
    if (spore) {
      for (let i=0;i<9;i++) {
        const angle=i*2.4+now*.7;
        const radius=8+i*5;
        AbyssArt.ellipse(g,field.x+Math.cos(angle)*radius,field.y+Math.sin(angle)*radius,3+i%4,3+i%4,'#c9db7855');
      }
    }
  }
  for (const item of game.loot) AbyssArt.beacon(g,item.x,item.y);
  for (const e of game.enemies) {
    AbyssArt.creature(g,e.x,e.y,e.angle,e.kind,e.flash>0);
    if (e.kind==='warden') {
      g.fillStyle='#361c2b';
      g.fillRect(e.x-30,e.y-33,60,5);
      g.fillStyle='#ef9ab1';
      g.fillRect(e.x-30,e.y-33,60*e.hp/e.maxHp,5);
    }
  }
  AbyssArt.diver(g,p.x,p.y,p.angle,game.invulnerable>.55);
  for (const effect of game.effects) {
    const ratio=effect.life/effect.max;
    g.globalAlpha=ratio;
    if (effect.points) {
      AbyssArt.line(g,effect.points,effect.color,8);
      AbyssArt.line(g,effect.points,'#fff9df',2);
    } else {
      const radius=effect.r*(1-.65*ratio);
      AbyssArt.ellipse(g,effect.x,effect.y,radius,radius,null,effect.color);
      g.globalAlpha=ratio*.17;
      AbyssArt.ellipse(g,effect.x,effect.y,radius*.84,radius*.84,effect.color);
    }
  }
  g.restore();
  if (pointer) {
    AbyssArt.ellipse(g,pointer.x,pointer.y,42,42,'#11353388','#8ce4ce');
    AbyssArt.ellipse(g,pointer.x+pointer.dx*36,pointer.y+pointer.dy*36,14,14,'#9cedd7b3','#d9fff0');
  }
}

function resize() {
  const bounds=canvas.getBoundingClientRect();
  view={w:bounds.width,h:bounds.height,dpr:Math.min(2,window.devicePixelRatio||1)};
  canvas.width=Math.round(view.w*view.dpr);
  canvas.height=Math.round(view.h*view.dpr);
}
for (const w of weapons) {
  const button=document.createElement('button');
  button.className='weapon';
  button.type='button';
  button.setAttribute('aria-pressed',String(w===selected));
  const icon=document.createElement('canvas');
  icon.width=84;
  icon.height=68;
  icon.style.width='42px';
  icon.style.height='34px';
  icon.setAttribute('aria-hidden','true');
  const ctx=icon.getContext('2d');
  ctx.scale(2,2);
  AbyssArt.icon(ctx,w);
  const name=document.createElement('strong');
  name.textContent=w.name;
  const role=document.createElement('span');
  role.textContent=w.role;
  button.append(icon,name,role);
  button.addEventListener('click',()=>{
    if (w===selected) return;
    $('arsenal').querySelector('[aria-pressed="true"]').setAttribute('aria-pressed','false');
    button.setAttribute('aria-pressed','true');
    selected=w;
    game.cooldown=0;
  });
  $('arsenal').append(button);
}
$('restart').addEventListener('click',()=>reset(true));
window.addEventListener('keydown',event=>{
  const key=event.key.toLowerCase();
  if (!game.running || !Object.hasOwn(directions,key) || event.metaKey || event.ctrlKey || event.altKey) return;
  event.preventDefault();
  keys.add(key);
});
window.addEventListener('keyup',event=>keys.delete(event.key.toLowerCase()));
window.addEventListener('blur',()=>{ keys.clear(); pointer=null; });
canvas.addEventListener('pointerdown',event=>{
  if (!game.running || pointer || event.button!==0) return;
  canvas.setPointerCapture(event.pointerId);
  const rect=canvas.getBoundingClientRect();
  pointer={id:event.pointerId,x:event.clientX-rect.left,y:event.clientY-rect.top,dx:0,dy:0};
});
canvas.addEventListener('pointermove',event=>{
  if (pointer?.id!==event.pointerId) return;
  const rect=canvas.getBoundingClientRect();
  const dx=event.clientX-rect.left-pointer.x,dy=event.clientY-rect.top-pointer.y;
  const length=Math.max(38,Math.hypot(dx,dy));
  pointer.dx=dx/length;
  pointer.dy=dy/length;
});
for (const name of ['pointerup','pointercancel','lostpointercapture']) {
  canvas.addEventListener(name,event=>{ if (pointer?.id===event.pointerId) pointer=null; });
}
reset(false);
resize();
new ResizeObserver(resize).observe(canvas);
let previous=performance.now();
let lastHud=0;
function frame(now) {
  if (game.running) simulate(clamp((now-previous)/1000,0,.05));
  if (now-lastHud>110) { updateHud(); lastHud=now; }
  previous=now;
  render(now/1000);
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
