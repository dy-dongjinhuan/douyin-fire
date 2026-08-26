/* =========================================================
   douyin-fire 通用动效层 · effects.js
   星座粒子背景 + 磁性按钮 + 涟漪 + 卡片 3D tilt 光晕
   非侵入式：自动增强现有元素，出错不影响页面功能
   ========================================================= */
(function(){
  'use strict';
  var RM = window.matchMedia('(prefers-reduced-motion:reduce)').matches;
  var FINE = window.matchMedia('(hover:hover) and (pointer:fine)').matches;
  var ACCENT = 'rgba(254,44,85,';

  // ---------- 0. 星座粒子背景（固定底层，带入所有页面） ----------
  (function(){
    if (document.getElementById('fx-stars') || RM) return;
    var canvas = document.createElement('canvas');
    canvas.id = 'fx-stars';
    canvas.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;z-index:0;pointer-events:none;';
    document.body.insertBefore(canvas, document.body.firstChild);

    var ctx = canvas.getContext('2d');
    if (!ctx) return;
    var DPR = Math.min(window.devicePixelRatio || 1, 2);
    var SPACING = 90, MOUSE_RADIUS = 140;
    var points = [], cols = 0, rows = 0, W = 0, H = 0;
    var mouse = { x: NaN, y: NaN };
    var running = false, raf = 0, last = 0, FPS = 1000 / 30, resizeTimer = null;

    function build(){
      cols = Math.ceil(W / SPACING) + 1;
      rows = Math.ceil(H / SPACING) + 1;
      var ox = (W - (cols - 1) * SPACING) / 2;
      var oy = (H - (rows - 1) * SPACING) / 2;
      points = [];
      for (var r = 0; r < rows; r++)
        for (var c = 0; c < cols; c++){
          var x = ox + SPACING * c, y = oy + SPACING * r;
          points.push({ rx: x, ry: y, x: x, y: y, vx: 0, vy: 0 });
        }
    }
    function resize(){
      W = window.innerWidth; H = window.innerHeight;
      canvas.width = W * DPR; canvas.height = H * DPR;
      ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
      build(); last = 0; running = true;
    }
    function tick(now){
      if (!running) return;
      if (now - last < FPS){ raf = requestAnimationFrame(tick); return; }
      last = now - (now - last) % FPS;
      ctx.clearRect(0, 0, W, H);
      var mx = mouse.x, my = mouse.y, maxSpeed = 0;
      for (var i = 0; i < points.length; i++){
        var p = points[i];
        var dx = p.x - mx, dy = p.y - my, d = Math.sqrt(dx*dx + dy*dy);
        if (d < MOUSE_RADIUS && d > 0.1){
          var f = (1 - d / MOUSE_RADIUS) * 30;
          p.vx += (dx / d) * f * 0.1; p.vy += (dy / d) * f * 0.1;
        }
        p.vx += 0.05 * (p.rx - p.x); p.vy += 0.05 * (p.ry - p.y);
        p.vx *= 0.85; p.vy *= 0.85;
        p.x += p.vx; p.y += p.vy;
        var s = Math.abs(p.vx) + Math.abs(p.vy);
        if (s > maxSpeed) maxSpeed = s;
      }
      ctx.strokeStyle = 'rgba(255,150,175,0.06)';
      ctx.lineWidth = 0.5;
      var gap = 10;
      for (var r = 0; r < rows; r++)
        for (var c = 0; c < cols - 1; c++){
          var a = points[r*cols+c], b = points[r*cols+c+1];
          var dx = b.x-a.x, dy = b.y-a.y, d = Math.sqrt(dx*dx+dy*dy);
          if (d < 20) continue;
          var ux = dx/d, uy = dy/d;
          ctx.beginPath(); ctx.moveTo(a.x+gap*ux, a.y+gap*uy); ctx.lineTo(b.x-gap*ux, b.y-gap*uy); ctx.stroke();
        }
      for (var c2 = 0; c2 < cols; c2++)
        for (var r2 = 0; r2 < rows - 1; r2++){
          var a2 = points[r2*cols+c2], b2 = points[(r2+1)*cols+c2];
          var dx2 = b2.x-a2.x, dy2 = b2.y-a2.y, d2 = Math.sqrt(dx2*dx2+dy2*dy2);
          if (d2 < 20) continue;
          var ux2 = dx2/d2, uy2 = dy2/d2;
          ctx.beginPath(); ctx.moveTo(a2.x+gap*ux2, a2.y+gap*uy2); ctx.lineTo(b2.x-gap*ux2, b2.y-gap*uy2); ctx.stroke();
        }
      ctx.fillStyle = 'rgba(255,150,175,0.10)';
      for (var j = 0; j < points.length; j++){
        var p = points[j], size = 1.6, alpha = 0.10;
        if (!isNaN(mx) && !isNaN(my)){
          var dx = p.x-mx, dy = p.y-my, d = Math.sqrt(dx*dx+dy*dy);
          var t = Math.max(0, 1 - d/MOUSE_RADIUS);
          size = 1.6 + 2*t; alpha = 0.12 + 0.35*t;
        }
        ctx.globalAlpha = alpha;
        ctx.fillRect(p.x-size, p.y-size, size*2, size*2);
      }
      ctx.globalAlpha = 1;
      if (maxSpeed < 0.01) running = false; else raf = requestAnimationFrame(tick);
    }
    function start(){ if (!running){ running = true; last = 0; raf = requestAnimationFrame(tick); } }
    window.addEventListener('mousemove', function(e){ mouse.x = e.clientX; mouse.y = e.clientY; start(); });
    window.addEventListener('resize', function(){ clearTimeout(resizeTimer); resizeTimer = setTimeout(resize, 150); });
    resize(); raf = requestAnimationFrame(tick);
  })();

  // ---------- 1. 卡片 3D tilt + 鼠标光晕（幅度缩小） ----------
  if (FINE && !RM) {
    document.querySelectorAll('.card, .panel, .feat, .step, .t, .friend-card, .stat-card').forEach(function(card){
      if (card.querySelector('.fx-glow')) return;
      if (getComputedStyle(card).position === 'static') card.style.position = 'relative';
      var glow = document.createElement('div');
      glow.className = 'fx-glow';
      card.appendChild(glow);
      card.addEventListener('mousemove', function(e){
        var r = card.getBoundingClientRect();
        var x = e.clientX - r.left, y = e.clientY - r.top;
        var cx = r.width / 2, cy = r.height / 2;
        glow.style.opacity = 1;
        glow.style.background = 'radial-gradient(circle 160px at ' + x + 'px ' + y + 'px, ' + ACCENT + '0.14), transparent 70%)';
        var rx = (cy - y) / cy * 0.8;
        var ry = (x - cx) / cx * 0.8;
        card.style.transform = 'perspective(800px) rotateX(' + rx + 'deg) rotateY(' + ry + 'deg) scale(1.002)';
        card.style.transition = 'transform 0.1s ease-out';
      });
      card.addEventListener('mouseleave', function(){
        glow.style.opacity = 0;
        card.style.transform = '';
        card.style.transition = 'transform 0.4s ease-out';
      });
    });
  }

  // ---------- 2. 磁性按钮 ----------
  if (FINE && !RM) {
    document.querySelectorAll('button, .btn, a.btn').forEach(function(el){
      el.addEventListener('mousemove', function(e){
        var r = el.getBoundingClientRect();
        var x = e.clientX - r.left - r.width / 2;
        var y = e.clientY - r.top - r.height / 2;
        el.style.transition = 'transform 0.1s ease-out';
        el.style.transform = 'translate(' + (x * 0.2) + 'px,' + (y * 0.3) + 'px)';
      });
      el.addEventListener('mouseleave', function(){
        el.style.transition = 'transform 0.4s ease';
        el.style.transform = '';
      });
    });
  }

  // ---------- 3. 涟漪 ----------
  document.querySelectorAll('button, .btn').forEach(function(el){
    if (el.__fxRipple) return;
    el.__fxRipple = true;
    el.addEventListener('click', function(e){
      var r = el.getBoundingClientRect();
      var s = document.createElement('span');
      s.className = 'fx-ripple';
      var size = Math.max(r.width, r.height);
      s.style.width = s.style.height = size + 'px';
      s.style.left = (e.clientX - r.left - size / 2) + 'px';
      s.style.top = (e.clientY - r.top - size / 2) + 'px';
      el.appendChild(s);
      setTimeout(function(){ s.remove(); }, 600);
    });
  });
})();
