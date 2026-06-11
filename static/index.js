const WIDTH = 20;
const HEIGHT = 10;
const GAME_BOX = document.getElementById('game-container');
const INITIAL_CONTENT = GAME_BOX.innerHTML;
let sessionId = null;
let lastDifficulty = 'EASY';

// Derive the deployment base path from the current URL so the app works
// both at "/" (local) and behind a reverse-proxy prefix like "/cosmic-conquest".
// The page is served at the base path; strip everything from "/static" onward
// if present, otherwise fall back to the directory of the current path.
function detectBasePath() {
    const path = window.location.pathname;
    const staticIdx = path.indexOf('/static');
    if (staticIdx >= 0) return path.substring(0, staticIdx);
    const apiIdx = path.indexOf('/api');
    if (apiIdx >= 0) return path.substring(0, apiIdx);
    return path.replace(/\/[^/]*$/, '');
}
const BASE_PATH = detectBasePath();

const manualMarkdown = `
# Cosmic Conquest: Commander's Manual

Hi Commander! This is your spaceship and you are flying around the galaxy. Your job is to **find new planets, build bases, and beat the bad guy robots called Cylons**. Here's how to play!

## The Numbers at the Top
These are your scores. Keep an eye on them!
* **SHIPS** 🚀 - How many little spaceships you have. You need them to fight and build stuff. The more you have, the better!
* **HULL** 💪 - How strong your ship is. If this hits zero, you lose! Be careful around black holes.
* **PLANETS** 🪐 - How many planets you've taken over. More planets = more ships for free every turn.
* **FUEL** ⛽ - What you need to move. If you run out, your ship just drifts and gets hurt.
* **TURN** 🔄 - How many turns have passed. You get free ships every turn, so bigger number = stronger you.

## Moving Around
* Press **W** to go UP
* Press **S** to go DOWN
* Press **A** to go LEFT
* Press **D** to go RIGHT

You can also use the arrow keys on your keyboard! Moving costs fuel.

## What To Do
* **[R] Report** - Look closely at the square you're on. It tells you what's there.
* **[P] Pulse Scan** - Spend 10 ships to see a big area around you. Good for exploring!
* **[C] Colonize** - Take over a planet you found! Make sure there are no Cylons on it first.
* **[F] Fight** - Punch the Cylons in your square! Try not to fight big groups.
* **[X] Scrap** - Turn 10 ships into 25 fuel. If you're running low on gas, do this!
* **[H] Hull Repair** - Pay 20 ships to fix your ship. Only use it in an emergency!
* **[N] New Game** - Start over. Pick your difficulty first!
* **[M] Manual** - Open and close this help screen.

## Building Stuff
You can build cool things on planets you own! Move to one of your planets and press:
* **[1] Shipyard** - Makes 3 extra ships per turn. Super useful!
* **[2] Sensor Array** - Automatically shows you what's nearby every turn. Saves you from getting lost.
* **[3] Orbital Battery** - Defends you from Cylon attacks. The bad guys will lose ships when they hit you!

Each building costs 20 ships to make. You can only build ONE thing per planet.

## Cool Things In Space
While exploring you might find these special squares:
* **@ (Gas Cloud)** - Free fuel! Move onto it and your tank fills up.
* **B (Black Hole)** - DANGER! Don't go here! It hurts your ship and flings you somewhere random.
* **N (Nebula)** - A cloud you can hide inside. The Hunter can't see you here, but you also can't see anything.
* **R (Ancient Relic)** - Super cool old alien tech! It gives you a permanent power-up like better weapons or a faster engine.

## The Bad Guys
There are mean robot enemies called **Cylons** running around. Watch out for:
* **C (Cylon Base)** - A planet owned by Cylons. If you want it, send lots of ships and fight them.
* **H (Hunter-Killer)** - A super scary robot that chases you around the map. It will start appearing after a while. Hide in Nebulas, build Batteries, or just run away!

## Running Out of Gas ⛽
If your **FUEL** hits zero and you try to move, your ship starts **drifting**! Here's what happens:
* Your ship flies in a random direction on its own.
* Your **HULL** drops by 2 every turn you drift.
* If you bump into a **Gas Cloud** while drifting, you get fuel back! Drift a lot and you might find one.
* If your **HULL** hits zero, it's game over!

**Tip:** Keep an eye on your fuel. If you're running low, press **[X]** to scrap 10 ships into 25 fuel. It's also smart to leave a Gas Cloud nearby in case you get stuck!

## How Hard Should It Be?
Before you press **[N]** for a new game, click one of the three buttons (**EASY / MEDIUM / HARD**) at the bottom of the screen. EASY is great if you've never played before. HARD is for when you get really good!

## Winning
You win by **taking over every Cylon base** on the map. Build up, explore, get stronger, and don't let them catch you!

Good luck, Commander! 🚀
`;

document.getElementById('markdown-container').innerHTML = marked.parse(manualMarkdown);

function openDifficultyModal() {
    const modal = document.getElementById('difficulty-modal');
    const radios = modal.querySelectorAll('input[name="difficulty"]');
    radios.forEach(r => { r.checked = (r.value === lastDifficulty); });
    modal.querySelectorAll('.diff-option').forEach(opt => {
        opt.classList.toggle('selected', opt.dataset.difficulty === lastDifficulty);
    });
    modal.style.display = 'block';
}

function closeDifficultyModal() {
    document.getElementById('difficulty-modal').style.display = 'none';
}

function attachDifficultyModalHandlers() {
    const modal = document.getElementById('difficulty-modal');
    modal.querySelectorAll('.diff-option').forEach(opt => {
        opt.addEventListener('click', () => {
            const diff = opt.dataset.difficulty;
            modal.querySelectorAll('input[name="difficulty"]').forEach(r => {
                r.checked = (r.value === diff);
            });
            modal.querySelectorAll('.diff-option').forEach(o => {
                o.classList.toggle('selected', o.dataset.difficulty === diff);
            });
        });
    });
    document.getElementById('diff-cancel').addEventListener('click', closeDifficultyModal);
    document.getElementById('diff-ok').addEventListener('click', async () => {
        const checked = modal.querySelector('input[name="difficulty"]:checked');
        const difficulty = checked ? checked.value : 'EASY';
        lastDifficulty = difficulty;
        closeDifficultyModal();
        await startNewGame(difficulty);
    });
}
attachDifficultyModalHandlers();

async function startNewGame(difficulty) {
    const res = await fetch(`${BASE_PATH}/api/new_game?difficulty=${encodeURIComponent(difficulty)}`, { method: 'POST' });
    const state = await res.json();
    sessionId = state.session_id;
    GAME_BOX.innerHTML = INITIAL_CONTENT;
    render(state);
}

async function newGame() {
    openDifficultyModal();
}

async function sendAction(action, params = {}) {
    if (!sessionId) {
        console.error("No active session.");
        return;
    }

    const body = { session_id: sessionId, ...params };

    const res = await fetch(`${BASE_PATH}/api/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });

    if (res.ok) {
        const state = await res.json();
        render(state);
    } else {
        console.error("Error from server:", await res.text());
        document.getElementById('log').innerText = "ERROR: Could not contact server.";
    }
}

function render(state) {
    if (state.game_over) {
        let msg = state.victory ?
            "<h1 style='color:cyan'>GALAXY LIBERATED!</h1><p>Total Victory achieved.</p>" :
            `<h1 style='color:red'>MISSION FAILED</h1><p>${state.log}</p>`;

        GAME_BOX.innerHTML = msg + "<button onclick='newGame()'>PLAY AGAIN</button>";
        return;
    }

    const player = state.player;
    const galaxy = state.galaxy;

    document.getElementById('ship-count').innerText = player.ships;
    document.getElementById('hull-count').innerText = player.hull;
    document.getElementById('planet-count').innerText = state.planets_owned;
    document.getElementById('fuel-count').innerText = player.fuel;
    document.getElementById('turn-count').innerText = player.turns;
    document.getElementById('pos-display').innerText = `${player.x},${player.y}`;
    const diffEl = document.getElementById('difficulty-display');
    diffEl.innerText = state.difficulty || 'EASY';
    diffEl.style.color = state.difficulty === 'HARD' ? '#f00' : (state.difficulty === 'MEDIUM' ? '#ff0' : '#0f0');
    document.getElementById('log').innerText = state.log;

    const lsElement = document.getElementById('ls-count');
    lsElement.innerText = state.ls_maintenance;
    const lsContainer = lsElement.parentElement;
    if (state.ls_maintenance <= 5) {
        lsContainer.style.color = '#0f0';
    } else if (state.ls_maintenance <= 15) {
        lsContainer.style.color = '#ff0';
    } else {
        lsContainer.style.color = '#f00';
    }

    document.getElementById('fuel-count').style.color = player.fuel < 5 ? 'red' : '#fff';
    document.getElementById('hull-count').style.color = player.hull < 30 ? 'red' : '#fff';

    let mapStr = "";
    for(let y=0; y<HEIGHT; y++) {
        for(let x=0; x<WIDTH; x++) {
            let key = `${x},${y}`;

            if(state.hunter_active && state.hunter_x === x && state.hunter_y === y && !(x === player.x && y === player.y)) {
                mapStr += "<span style='color:red; font-weight:bold; text-shadow: 0 0 10px red'>H</span>";
                continue;
            }

            if(x === player.x && y === player.y) {
                mapStr += "<span class='highlight'>X</span>";
            } else if(galaxy[key]) {
                let s = galaxy[key];
                if(s.anomaly === 'black_hole') mapStr += "<span style='color:magenta; text-shadow: 0 0 10px magenta'>B</span>";
                else if(!s.scanned) mapStr += "?";
                else if(s.is_gas_cloud) {
                    let color = "#fff";
                    if(s.owner === 'Player') color = "#0af";
                    else if(s.owner === 'Cylon') color = "#f00";
                    mapStr += `<span style='color: ${color}'>@</span>`;
                }
                else if(s.anomaly === 'nebula') mapStr += "<span style='color:cyan'>N</span>";
                else if(s.anomaly === 'relic') mapStr += "<span style='color:gold; text-shadow: 0 0 10px gold'>R</span>";
                else if(s.owner === 'Player') {
                    if (s.infrastructure === 'shipyard') mapStr += "<span class='player'>Y</span>";
                    else if (s.infrastructure === 'sensor') mapStr += "<span class='player'>S</span>";
                    else if (s.infrastructure === 'battery') mapStr += "<span class='player'>D</span>";
                    else mapStr += "<span class='player'>P</span>";
                }
                else if(s.owner === 'Cylon') mapStr += "<span class='enemy'>C</span>";
                else mapStr += "o";
            } else {
                mapStr += ".";
            }
        }
        mapStr += "<br>";
    }
    document.getElementById('map').innerHTML = mapStr;
}

window.addEventListener('keydown', (e) => {
    if(["ArrowUp","ArrowDown","ArrowLeft","ArrowRight", " "].indexOf(e.code) > -1) e.preventDefault();

    const key = e.key.toLowerCase();
    if (document.getElementById('manual-modal').style.display === 'block') {
        if (key === 'm' || key === 'escape') document.getElementById('manual-modal').style.display = 'none';
        return;
    }

    if (key === 'w' || key === 'arrowup') sendAction('move', { direction: 'w' });
    else if (key === 's' || key === 'arrowdown') sendAction('move', { direction: 's' });
    else if (key === 'a' || key === 'arrowleft') sendAction('move', { direction: 'a' });
    else if (key === 'd' || key === 'arrowright') sendAction('move', { direction: 'd' });
    else if (key === 'p') sendAction('pulse');
    else if (key === 'r') sendAction('report');
    else if (key === 'f') sendAction('fight');
    else if (key === 'c') sendAction('colonize');
    else if (key === '1') sendAction('build', { build_type: 'shipyard' });
    else if (key === '2') sendAction('build', { build_type: 'sensor' });
    else if (key === '3') sendAction('build', { build_type: 'battery' });
    else if (key === 'm') document.getElementById('manual-modal').style.display = 'block';
    else if (key === 'x') sendAction('scrap');
    else if (key === 'h') sendAction('repair');
    else if (key === 'n') newGame();
});

newGame();
