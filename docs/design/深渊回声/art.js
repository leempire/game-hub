'use strict';

const AbyssArt = (() => {
  const tau = Math.PI * 2;
  function outline(g, fill, stroke, draw, width = 1.5) {
    g.beginPath();
    draw(g);
    if (fill) { g.fillStyle = fill; g.fill(); }
    if (stroke) { g.strokeStyle = stroke; g.lineWidth = width; g.stroke(); }
  }
  function ellipse(g, x, y, rx, ry, fill, stroke) {
    outline(g, fill, stroke, p => p.ellipse(x, y, rx, ry, 0, 0, tau));
  }
  function line(g, points, stroke, width = 1) {
    outline(g, null, stroke, p => {
      p.moveTo(...points[0]);
      for (const point of points.slice(1)) p.lineTo(...point);
    }, width);
  }
  function random(seed) {
    return () => {
      seed = (Math.imul(1664525, seed) + 1013904223) >>> 0;
      return seed / 4294967296;
    };
  }
  function floor(width, height) {
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const g = canvas.getContext('2d');
    const rand = random(931);
    g.fillStyle = '#0b252b';
    g.fillRect(0, 0, width, height);
    for (let row = 0; row < height / 56; row++) {
      for (let col = -1; col < width / 72; col++) {
        const x = col * 72 + (row % 2) * 36;
        const y = row * 56;
        g.fillStyle = ['#132e34', '#102930', '#112b31', '#0f272d'][Math.floor(rand() * 4)];
        g.fillRect(x + 1, y + 1, 70, 54);
        line(g, [[x + 5, y + 50], [x + 5, y + 5], [x + 67, y + 5]], '#204046');
        for (const offset of [9, 63]) ellipse(g, x + offset, y + 10, 1.4, 1.4, '#365054');
      }
    }
    for (let i = 0; i < 8200; i++) {
      g.fillStyle = i % 3 ? '#527c771e' : '#01090b70';
      g.fillRect(rand() * width, rand() * height, 1 + rand() * 4, 1 + rand() * 2);
    }
    for (let i = 0; i < 180; i++) {
      const x = rand() * width, y = rand() * height;
      line(g, [[x, y], [x + 11, y + 2], [x + 26, y - 2]], '#3c827827', 2);
    }
    for (let i = 0; i < 32; i++) {
      const x = rand() * width, y = rand() * height;
      if (Math.abs(x-width/2) < 230 && Math.abs(y-height/2) < 170) continue;
      g.save();
      g.translate(x, y);
      g.rotate(rand() * tau);
      if (i % 3 === 0) {
        ellipse(g, 0, 0, 44, 27, '#020f17a0');
        outline(g, '#213b42', '#3d5d61', p => p.roundRect(-39,-23,78,42,8), 2);
        outline(g, '#0b1b24', '#626d62', p => p.roundRect(-29,-13,57,22,4));
        for (let k = -23; k <= 22; k += 10) line(g, [[k,-11],[k,7]], '#324d4d', 3);
        line(g, [[-33,15],[-10,15]], '#b38950', 2);
      } else {
        for (let j = 0; j < 7; j++) {
          const spread = j * 9 - 27;
          outline(g, null, j % 2 ? '#377165' : '#28514d', p => {
            p.moveTo(0, 10);
            p.quadraticCurveTo(spread * 0.33, -12, spread, -18 - rand() * 18);
          }, 2 + rand() * 2);
        }
      }
      g.restore();
    }
    g.save();
    g.translate(width / 2, height / 2);
    ellipse(g, 0, 0, 85, 85, '#1b3f4433', '#466966');
    ellipse(g, 0, 0, 73, 73, null, '#375752');
    g.setLineDash([3, 10]);
    ellipse(g, 0, 0, 94, 94, null, '#6d8074');
    g.setLineDash([]);
    for (let i = 0; i < 4; i++) {
      g.rotate(tau / 4);
      line(g, [[-15,-79],[15,-79]], '#b18b56', 3);
    }
    g.fillStyle = '#6b8581';
    g.font = 'bold 12px monospace';
    g.textAlign = 'center';
    g.fillText('RECOVERY / 04', 0, 64);
    g.restore();
    return canvas;
  }
  function diver(g, x, y, angle, hurt = false) {
    g.save();
    g.translate(x, y);
    ellipse(g, 2, 5, 23, 17, '#01080bbd');
    g.rotate(angle);
    line(g, [[-6,-9],[-17,-12],[-21,-9]], '#6f8d84', 5);
    line(g, [[-6,9],[-17,12],[-21,9]], '#6f8d84', 5);
    line(g, [[-12,-5],[-12,5]], '#ca995d', 11);
    ellipse(g, 0, 0, 16, 14, hurt ? '#cf796d' : '#abbdb0', '#273635');
    ellipse(g, 4, 0, 13, 12, '#30494b', '#dcba80');
    ellipse(g, 8, 0, 8, 8, '#235c62', '#7edacc');
    line(g, [[8,-5],[12,-3],[13,1]], '#d4fff0', 2);
    line(g, [[9,12],[18,9],[22,9]], '#cdb17b', 4);
    g.restore();
  }
  function creature(g, x, y, angle, kind, flash = false) {
    g.save();
    g.translate(x, y);
    g.rotate(angle);
    if (kind === 'warden') g.scale(1.45, 1.45);
    if (kind === 'eel') {
      outline(g, null, '#452c64', p => { p.moveTo(-24, 6); p.bezierCurveTo(-13,-16,-10,12,10,0); }, 10);
      line(g, [[-23,6],[-15,0],[-9,1]], '#aa70c2', 2);
      ellipse(g, 7, 0, 12, 7, flash ? '#f5e7ff' : '#796197', '#342348');
      ellipse(g, 13, -2, 2, 2, '#ffbddb');
    } else if (kind === 'crab' || kind === 'warden') {
      const big = kind === 'warden';
      for (const sign of [-1,1]) {
        for (let i = 0; i < 3; i++) line(g, [[4 - i * 6,6 * sign],[-1 - i * 7,14 * sign],[-8 - i * 7,18 * sign]], big ? '#86435b' : '#7f4b37', 3);
        line(g, [[7,7*sign],[15,13*sign],[21,9*sign]], big ? '#d57d9c' : '#ce825e', 5);
      }
      ellipse(g, 0, 0, 15, 12, flash ? '#fff2e5' : big ? '#9b4e6f' : '#ad664b', '#482b33');
      line(g, [[-11,-5],[-1,-8],[8,-4]], big ? '#f2a3b8' : '#e1a075', 2);
      for (const side of [-1,1]) ellipse(g, 10, side * 4, 2.2, 2.2, '#fff2b2');
    } else {
      for (const offset of [-6,0,6]) {
        outline(g, null, '#62a99e', p => { p.moveTo(-3,offset); p.quadraticCurveTo(-12,offset-4,-20,offset+3); }, 2);
      }
      ellipse(g, 0, 0, 12, 11, flash ? '#e8ffff' : '#569f98', '#95d0bb');
      ellipse(g, 4, 0, 5, 7, '#316463');
      ellipse(g, 6, -2, 2, 2, '#edffbb');
    }
    g.restore();
  }
  function beacon(g, x, y) {
    ellipse(g, x, y, 9, 9, '#57d9f328');
    outline(g, '#7eeeff', '#cdfaff', p => {
      p.moveTo(x, y-5); p.lineTo(x+4, y); p.lineTo(x,y+5); p.lineTo(x-4,y); p.closePath();
    });
  }
  function icon(g, weapon) {
    g.save();
    g.translate(21, 17);
    g.lineCap = 'round';
    const c = weapon.color;
    switch (weapon.id) {
      case 'rivet': line(g, [[-12,5],[6,-4],[11,-4]], c, 5); line(g, [[-3,2],[-1,8]], '#b3c5bd', 3); break;
      case 'arc': line(g, [[-12,4],[-4,-7],[1,1],[7,-7],[12,-3]], c, 2.5); break;
      case 'ring': ellipse(g, 0, 0, 12, 8, null, c); ellipse(g, 9, -5, 3, 3, c); break;
      case 'spore': for (const [x,y,r] of [[-8,3,5],[1,-3,7],[9,4,5]]) ellipse(g,x,y,r,r,c,'#586949'); break;
      case 'charge': ellipse(g, 0, 1, 8, 9, '#3e292a', c); line(g, [[0,-8],[5,-13],[9,-11]], c, 2); break;
      case 'harpoon': line(g, [[-12,9],[10,-8],[3,-7]], c, 2.5); line(g, [[10,-8],[8,-1]], c, 2.5); break;
    }
    g.restore();
  }
  return { floor, diver, creature, beacon, icon, line, ellipse, outline };
})();
