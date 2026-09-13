var API = "/api";
var P = [], A = [];

// Nicad ciblé pour zoom+surbrillance sur la carte (mis par voirSurCarte)
var focusNicad = null;

// Ids des alertes créées lors du dernier import/detection (surbrillance carte)
var nouvellesAlertesIds = [];


// ─────────────────────────────────────────────────────────────────────────────
// ECHAPPEMENT HTML (anti-XSS)
// ─────────────────────────────────────────────────────────────────────────────

function esc(str) {
  var div = document.createElement("div");
  div.textContent = str === null || str === undefined ? "" : String(str);
  return div.innerHTML;
}


// ─────────────────────────────────────────────────────────────────────────────
// AUTHENTIFICATION
// ─────────────────────────────────────────────────────────────────────────────

function doLogin(username, password) {

  return fetch(API + "/auth/login", {
    method: "POST",

    headers: {
      "Content-Type": "application/json"
    },

    credentials: "include",

    body: JSON.stringify({
      username: username,
      password: password
    })
  })
  .then(function(r) {

    if (!r.ok) {
      throw new Error("Identifiants invalides");
    }

    return r.json();
  });
}


function showLoginOverlay() {
  document.getElementById("login-overlay").style.display = "flex";
}


function hideLoginOverlay() {
  document.getElementById("login-overlay").style.display = "none";
}


document
  .getElementById("login-form")
  .addEventListener("submit", function(e) {

    e.preventDefault();

    var form = this;
    var user = document.getElementById("login-user").value.trim();
    var pass = document.getElementById("login-pass").value;
    var errEl = document.getElementById("login-err");
    var btn = document.getElementById("login-submit");

    errEl.textContent = "";
    btn.classList.add("loading");
    btn.disabled = true;

    doLogin(user, pass)

      .then(function() {

        hideLoginOverlay();

        document.getElementById("login-pass").value = "";

        loadAll();
        obs();

      })

      .catch(function() {

        errEl.textContent = "Identifiant ou mot de passe incorrect.";

        form.classList.remove("login-form-shake");

        void form.offsetWidth;

        form.classList.add("login-form-shake");

      })

      .finally(function() {

        btn.classList.remove("loading");
        btn.disabled = false;

      });

  });


document
  .getElementById("btn-logout")
  .addEventListener("click", function() {

    fetch(API + "/auth/logout", {
      method: "POST",
      credentials: "include"
    })

    .then(function() {
      showLoginOverlay();
    })

    .catch(function() {
      showLoginOverlay();
    });

  });


function request(url, options) {

  options = options || {};

  options.credentials = "include";

  return fetch(API + url, options)
    .then(function(r) {

      if (r.status === 401) {

        showLoginOverlay();

        throw new Error("Authentification requise");
      }

      return r;
    });
}


function sc(s) {
  return s >= 0.8
    ? "r"
    : s >= 0.5
      ? "g"
      : "n";
}


function scol(s) {
  return s >= 0.8
    ? "#A82820"
    : s >= 0.5
      ? "#9A7420"
      : "#9A8868";
}


function pad(n, l) {
  return String(n).padStart(l, "0");
}


function showMsg(id, txt, type) {

  var el = document.getElementById(id);

  if (!el) return;

  el.textContent = txt;

  el.className = "msg " + type;
}


// ─────────────────────────────────────────────────────────────────────────────
// NAVIGATION VERS LA CARTE (clic sur une parcelle / alerte n'importe où)
// ─────────────────────────────────────────────────────────────────────────────

function voirSurCarte(nicad) {

  focusNicad = nicad;

  goSection("carte");

  buildCarte(true);
}


// ─────────────────────────────────────────────────────────────────────────────
// CHARGEMENT
// ─────────────────────────────────────────────────────────────────────────────

function loadAll() {

  Promise.all([

    request("/stats")
      .then(function(r) {
        return r.json();
      }),

    request("/alertes")
      .then(function(r) {
        return r.json();
      }),

    request("/parcelles")
      .then(function(r) {
        return r.json();
      })

  ])

  .then(function(res) {

    var stats = res[0];
    var alertes = res[1];
    var parcelles = res[2];

    A = alertes.map(function(a) {

      return {
        id: a.id_alerte,
        t: a.type_anomalie,
        n: a.nicad,
        d: a.description,
        s: parseFloat(a.score_risque),
        st: a.statut,
        dt: a.date_detection
      };

    });


    P = parcelles.map(function(p) {

      return {
        n: p.nicad,
        z: p.zone,
        sl: p.statut_legal,
        so: parseFloat(p.superficie_officielle),
        sr: parseFloat(p.superficie_reelle),
        na: parseInt(p.nb_alertes),
        sm: parseFloat(p.score_max),
        lat: parseFloat(p.lat),
        lon: parseFloat(p.lon)
      };

    });


    document.getElementById("vtotal").textContent =
      pad(stats.total_alertes, 3);

    document.getElementById("vcrit").textContent =
      pad(stats.critiques, 2);

    document.getElementById("vchev").textContent =
      pad(stats.chevauchements, 2);

    document.getElementById("vzone").textContent =
      pad(stats.zones_illegales, 2);

    document.getElementById("vparc").textContent =
      pad(stats.nb_parcelles, 2);


    var ali = document.getElementById("al-info");

    if (ali) {
      ali.textContent =
        pad(stats.total_alertes, 3)
        + " ALERTES EN BASE";
    }


    buildTable();
    buildAlertes();
    buildSidebar();

    if (mapBuilt) {
      buildCarte(true);
    }

    obs();

  })

  .catch(function(e) {

    console.error(
      "Erreur chargement:",
      e
    );

  });
}


// ─────────────────────────────────────────────────────────────────────────────
// TABLE
// ─────────────────────────────────────────────────────────────────────────────

function buildTable() {

  var top = P
    .slice()
    .sort(function(a, b) {
      return b.sm - a.sm;
    })
    .slice(0, 8);

  document.getElementById("vtcnt").textContent =
    top.length + " enregistrements";

  var rows = "";

  for (var i = 0; i < top.length; i++) {

    var p = top[i];

    var cl = sc(p.sm);

    var pct = Math.round(p.sm * 100);

    rows +=
      "<div class='trow sr' style='transition-delay:"
      + (i * 0.07)
      + "s' data-nicad='"
      + esc(p.n)
      + "'>";

    rows +=
      "<div class='tc b'>"
      + esc(p.n)
      + "</div>";

    rows +=
      "<div class='tc' data-label='Zone'>"
      + esc(p.z)
      + "</div>";

    rows +=
      "<div class='tc' data-label='Alertes'>"
      + pad(p.na, 2)
      + "</div>";

    rows +=
      "<div class='tc' data-label='Score'>"
      + "<div class='sbw'>"
      + "<span>"
      + p.sm.toFixed(3)
      + "</span>";

    rows +=
      "<div class='sbl'>"
      + "<div class='sbf "
      + cl
      + "' style='width:"
      + pct
      + "%'></div>"
      + "</div>"
      + "</div>"
      + "</div>";

    rows +=
      "<div class='tc' data-label='Statut'>"
      + "<span class='bdg "
      + cl
      + "'>"
      + esc((p.sl || "").toUpperCase())
      + "</span>"
      + "</div>"
      + "</div>";
  }

  document.getElementById("vtable").innerHTML =
    rows;

  // Toute la ligne "Vue générale" est cliquable -> direct sur la carte
  document
    .querySelectorAll("#vtable .trow")
    .forEach(function(el) {

      el.style.cursor = "pointer";

      el.addEventListener("click", function() {
        voirSurCarte(this.getAttribute("data-nicad"));
      });

    });
}


// ─────────────────────────────────────────────────────────────────────────────
// ALERTES
// ─────────────────────────────────────────────────────────────────────────────

var CF = "tous";


function buildAlertes() {

  var list =
    CF === "tous"
      ? A
      : A.filter(function(a) {
          return a.t === CF;
        });


  document.getElementById("altotal").textContent =
    pad(list.length, 3);


  var rows = "";


  for (var i = 0; i < list.length; i++) {

    var a = list[i];

    var cl = sc(a.s);

    var confHtml = "";


    if (a.st === "resolue") {

      confHtml =
        "<span class='tag-ok'>CONFIRME</span>";

    }

    else if (a.st === "fausse_alerte") {

      confHtml =
        "<span class='tag-ko'>REJETE</span>";

    }

    else {

      confHtml =
        "<div class='conf-wrap'>";

      confHtml +=
        "<button class='cbtn ok' "
        + "data-id='"
        + a.id
        + "' "
        + "data-st='resolue'>"
        + "Confirmer"
        + "</button>";

      confHtml +=
        "<button class='cbtn ko' "
        + "data-id='"
        + a.id
        + "' "
        + "data-st='fausse_alerte'>"
        + "Rejeter"
        + "</button>";

      confHtml += "</div>";
    }

    // Bouton "Localiser" toujours présent, quel que soit le statut
    confHtml +=
      "<button class='cbtn loc' data-nicad='"
      + esc(a.n)
      + "'>Localiser</button>";


    rows +=
      "<div class='arow sr' "
      + "style='transition-delay:"
      + (Math.min(i, 10) * 0.05)
      + "s'>";

    rows +=
      "<div>"
      + "<span class='abadge "
      + cl
      + "'>"
      + esc(a.t.replace(/_/g, " ").toUpperCase())
      + "</span>"
      + "</div>";


    rows +=
      "<div>"
      + "<div class='anicad'>"
      + esc(a.n)
      + "</div>"
      + "<div class='adesc'>"
      + esc(a.d)
      + "</div>"
      + "<div class='adate'>"
      + esc(a.dt || "")
      + "</div>"
      + "</div>";


    rows +=
      "<div class='ascore "
      + cl
      + "'>"
      + a.s.toFixed(3)
      + "</div>";


    rows +=
      "<div class='astatut'>"
      + esc(a.st.toUpperCase())
      + "</div>";


    rows +=
      "<div>"
      + confHtml
      + "</div>";

    rows += "</div>";
  }


  document.getElementById("alist").innerHTML =
    rows;


  document.querySelectorAll(".cbtn.ok, .cbtn.ko")
    .forEach(function(btn) {

      btn.addEventListener(
        "click",
        function() {

          confirmerAlerte(
            parseInt(
              this.getAttribute("data-id")
            ),

            this.getAttribute(
              "data-st"
            )
          );

        }
      );

    });


  document.querySelectorAll(".cbtn.loc")
    .forEach(function(btn) {

      btn.addEventListener(
        "click",
        function() {

          voirSurCarte(
            this.getAttribute("data-nicad")
          );

        }
      );

    });


  obs();
}


function confirmerAlerte(id, statut) {

  request(
    "/alertes/" + id + "/statut",
    {
      method: "PATCH",

      headers: {
        "Content-Type":
          "application/json"
      },

      body: JSON.stringify({
        statut: statut
      })
    }
  )

  .then(function() {

    loadAll();
    buildHistorique();

  })

  .catch(function(e) {

    console.error(
      "Erreur:",
      e
    );

  });
}


// ─────────────────────────────────────────────────────────────────────────────
// FILTRES
// ─────────────────────────────────────────────────────────────────────────────

document
  .getElementById("f-tous")
  .addEventListener(
    "click",
    function() {
      setFilt("tous", this);
    }
  );


document
  .getElementById("f-chev")
  .addEventListener(
    "click",
    function() {
      setFilt("chevauchement", this);
    }
  );


document
  .getElementById("f-doub")
  .addEventListener(
    "click",
    function() {
      setFilt("doublon_titre", this);
    }
  );


document
  .getElementById("f-zone")
  .addEventListener(
    "click",
    function() {
      setFilt("zone_illegale", this);
    }
  );


document
  .getElementById("f-ml")
  .addEventListener(
    "click",
    function() {
      setFilt("scoring_ml", this);
    }
  );


function setFilt(f, btn) {

  CF = f;

  document
    .querySelectorAll(".fbtn")
    .forEach(function(b) {
      b.classList.remove("on");
    });

  btn.classList.add("on");

  buildAlertes();
}


// ─────────────────────────────────────────────────────────────────────────────
// EXPORT PDF (registre des alertes)
// ─────────────────────────────────────────────────────────────────────────────

document
  .getElementById("btn-export-pdf")
  .addEventListener(
    "click",
    function() {

      var critiques =
        A.filter(function(a) {
          return a.s >= 0.8;
        });


      var now = new Date();

      var dateStr =
        now.toLocaleDateString(
          "fr-FR",
          {
            day: "2-digit",
            month: "2-digit",
            year: "numeric"
          }
        );


      var heureStr =
        now.toLocaleTimeString(
          "fr-FR",
          {
            hour: "2-digit",
            minute: "2-digit"
          }
        );


      var rows = "";


      for (var i = 0; i < A.length; i++) {

        var a = A[i];

        var col = scol(a.s);


        rows +=
          "<tr style='border-bottom:1px solid #D4C4A0'>";

        rows +=
          "<td style='padding:9px 12px;"
          + "font-family:monospace;"
          + "font-size:11px'>"
          + esc(a.n)
          + "</td>";


        rows +=
          "<td style='padding:9px 12px;"
          + "font-family:monospace;"
          + "font-size:10px;"
          + "color:"
          + col
          + "'>"
          + esc(a.t.replace(/_/g, " ").toUpperCase())
          + "</td>";


        rows +=
          "<td style='padding:9px 12px;"
          + "font-size:11px;"
          + "color:#5A4E38'>"
          + esc(a.d)
          + "</td>";


        rows +=
          "<td style='padding:9px 12px;"
          + "font-family:monospace;"
          + "font-size:13px;"
          + "color:"
          + col
          + ";text-align:right'>"
          + a.s.toFixed(3)
          + "</td>";


        rows +=
          "<td style='padding:9px 12px;"
          + "font-family:monospace;"
          + "font-size:10px;"
          + "text-align:right'>"
          + esc(a.st.toUpperCase())
          + "</td>";


        rows += "</tr>";
      }


      var doc =
        "<!DOCTYPE html>"
        + "<html>"
        + "<head>"
        + "<meta charset='UTF-8'>";


      doc +=
        "<style>"
        + "body{font-family:Georgia,serif;"
        + "padding:48px;color:#1E1A12;}"
        + "h1{font-family:monospace;"
        + "font-size:20px;"
        + "font-weight:400;"
        + "letter-spacing:.1em;"
        + "margin-bottom:4px;}"
        + ".sub{font-family:monospace;"
        + "font-size:9px;"
        + "letter-spacing:.16em;"
        + "color:#9A8868;"
        + "margin-bottom:28px;}"
        + ".meta{display:flex;"
        + "gap:40px;"
        + "margin-bottom:28px;"
        + "padding-bottom:18px;"
        + "border-bottom:1px solid #D4C4A0;}"
        + ".mk{font-family:monospace;"
        + "font-size:9px;"
        + "letter-spacing:.14em;"
        + "color:#9A8868;"
        + "margin-bottom:4px;}"
        + ".mv{font-family:monospace;"
        + "font-size:18px;"
        + "font-weight:300;}"
        + ".mv.r{color:#A82820;}"
        + "table{width:100%;"
        + "border-collapse:collapse;}"
        + "th{font-family:monospace;"
        + "font-size:9px;"
        + "letter-spacing:.12em;"
        + "color:#9A8868;"
        + "text-align:left;"
        + "padding:9px 12px;"
        + "border-bottom:2px solid #D4C4A0;}"
        + ".ft{margin-top:40px;"
        + "padding-top:16px;"
        + "border-top:1px solid #D4C4A0;"
        + "font-family:monospace;"
        + "font-size:9px;"
        + "color:#9A8868;"
        + "letter-spacing:.1em;}"
        + "@media print{body{padding:24px;}}"
        + "</style>"
        + "</head>"
        + "<body>";


      doc +=
        "<h1>SDFCS — RAPPORT DE DETECTION</h1>";


      doc +=
        "<div class='sub'>"
        + "SYSTEME DE DETECTION DE FRAUDE CADASTRALE "
        + "· DAKAR · SENEGAL"
        + "</div>";


      doc +=
        "<div class='meta'>";


      doc +=
        "<div>"
        + "<div class='mk'>DATE</div>"
        + "<div class='mv'>"
        + dateStr
        + "</div>"
        + "</div>";


      doc +=
        "<div>"
        + "<div class='mk'>HEURE</div>"
        + "<div class='mv'>"
        + heureStr
        + "</div>"
        + "</div>";


      doc +=
        "<div>"
        + "<div class='mk'>TOTAL ALERTES</div>"
        + "<div class='mv'>"
        + pad(A.length, 3)
        + "</div>"
        + "</div>";


      doc +=
        "<div>"
        + "<div class='mk'>CRITIQUES</div>"
        + "<div class='mv r'>"
        + pad(critiques.length, 2)
        + "</div>"
        + "</div>";


      doc += "</div>";


      doc +=
        "<table>"
        + "<thead>"
        + "<tr>"
        + "<th>PARCELLE</th>"
        + "<th>TYPE</th>"
        + "<th>DESCRIPTION</th>"
        + "<th>SCORE</th>"
        + "<th>STATUT</th>"
        + "</tr>"
        + "</thead>"
        + "<tbody>"
        + rows
        + "</tbody>"
        + "</table>";


      doc +=
        "<div class='ft'>"
        + "SDFCS · CEDT / LE G15 · "
        + "UTM ZONE 28N · "
        + dateStr
        + " "
        + heureStr
        + "</div>";


      doc +=
        "</body>"
        + "</html>";


      var win =
        window.open(
          "",
          "_blank"
        );


      win.document.write(doc);

      win.document.close();


      setTimeout(
        function() {
          win.print();
        },
        500
      );

    }
  );


// ─────────────────────────────────────────────────────────────────────────────
// EXPORT CARTE (vraie carte image, légende + titre, via GeoPandas côté API)
// ─────────────────────────────────────────────────────────────────────────────

var btnExportCarte = document.getElementById("btn-export-carte");

if (btnExportCarte) {

  btnExportCarte.addEventListener("click", function() {

    var btn = this;
    var original = btn.textContent;

    btn.textContent = "Génération...";
    btn.disabled = true;

    request("/export/carte")

      .then(function(r) {

        // Si l'API renvoie une erreur, la reponse est du JSON, pas une
        // image : on doit le detecter ici, sinon on telecharge un
        // fichier .png qui contient en realite un message d'erreur
        // texte (d'ou l'impression de "fichier corrompu").
        if (!r.ok) {

          return r.json()
            .catch(function() {
              return { detail: "Erreur inconnue" };
            })
            .then(function(err) {
              throw new Error(err.detail || "Erreur export");
            });
        }

        return r.blob();
      })

      .then(function(blob) {

        var url = URL.createObjectURL(blob);

        var a = document.createElement("a");
        a.href = url;
        a.download = "carte_sdfcs.png";

        document.body.appendChild(a);
        a.click();
        a.remove();

        URL.revokeObjectURL(url);

      })

      .catch(function(e) {

        alert(
          "Erreur export carte : "
          + (e && e.message ? e.message : "inconnue")
        );

      })

      .finally(function() {

        btn.textContent = original;
        btn.disabled = false;

      });

  });

}


// ─────────────────────────────────────────────────────────────────────────────
// PARCELLES
// ─────────────────────────────────────────────────────────────────────────────

function buildSidebar() {

  var rows = "";

  for (var i = 0; i < P.length; i++) {

    var p = P[i];

    var cl =
      p.sm >= 0.8
        ? "r"
        : p.sm >= 0.5
          ? "g"
          : "ok";


    rows +=
      "<div class='pitem' "
      + "data-idx='"
      + i
      + "' "
      + "id='pi"
      + i
      + "'>";


    rows +=
      "<div class='pnwrap'>"
      + "<div class='pind "
      + cl
      + "'></div>"
      + "<div class='pnicad'>"
      + esc(p.n)
      + "</div>"
      + "</div>";


    rows +=
      "<div class='pcnt'>"
      + p.na
      + " alt."
      + "</div>";


    rows += "</div>";
  }


  document.getElementById("pslist").innerHTML =
    rows;


  document
    .querySelectorAll(".pitem")
    .forEach(function(el) {

      el.addEventListener(
        "click",
        function() {

          showDetail(
            parseInt(
              this.getAttribute("data-idx")
            )
          );

        }
      );

    });
}


function showDetail(idx) {

  document
    .querySelectorAll(".pitem")
    .forEach(function(el) {
      el.classList.remove("on");
    });


  document
    .getElementById("pi" + idx)
    .classList.add("on");


  var p = P[idx];


  var als =
    A.filter(function(a) {
      return a.n === p.n;
    });


  var alh = "";


  for (var i = 0; i < als.length; i++) {

    var a = als[i];


    alh +=
      "<div class='pdalrow'>";


    alh +=
      "<span class='abadge "
      + sc(a.s)
      + "' style='flex-shrink:0;"
      + "white-space:nowrap'>"
      + esc(a.t.replace(/_/g, " ").toUpperCase())
      + "</span>";


    alh +=
      "<div style='flex:1;"
      + "font-size:11px;"
      + "color:#5A4E38;"
      + "line-height:1.5'>"
      + esc(a.d)
      + "</div>";


    alh +=
      "<div style='font-family:monospace;"
      + "font-size:13px;"
      + "min-width:50px;"
      + "text-align:right;"
      + "color:"
      + scol(a.s)
      + "'>"
      + a.s.toFixed(3)
      + "</div>";


    alh += "</div>";
  }


  var sc2 =
    p.sm >= 0.8
      ? "r"
      : p.sm >= 0.5
        ? "g"
        : "";


  var det =
    "<div class='pdetailwrap'>";


  det +=
    "<div style='display:flex;align-items:center;"
    + "justify-content:space-between;flex-wrap:wrap;gap:12px'>";

  det +=
    "<div class='pdnicad sr'>"
    + esc(p.n)
    + "</div>";

  det +=
    "<button class='ibtn' style='width:auto;padding:10px 20px' "
    + "id='btn-loc-detail'>Localiser sur la carte</button>";

  det += "</div>";


  det +=
    "<div class='pdzone sr d1'>"
    + esc(p.z)
    + " — "
    + esc((p.sl || "").toUpperCase())
    + "</div>";


  det +=
    "<div class='pdgrid sr d2'>";


  det +=
    "<div class='pdcell'>"
    + "<div class='pdkey'>"
    + "Superficie officielle"
    + "</div>"
    + "<div class='pdval'>"
    + p.so.toLocaleString()
    + " m2"
    + "</div>"
    + "</div>";


  det +=
    "<div class='pdcell'>"
    + "<div class='pdkey'>"
    + "Superficie reelle"
    + "</div>"
    + "<div class='pdval'>"
    + p.sr.toLocaleString()
    + " m2"
    + "</div>"
    + "</div>";


  det +=
    "<div class='pdcell'>"
    + "<div class='pdkey'>"
    + "Score max"
    + "</div>"
    + "<div class='pdval "
    + sc2
    + "'>"
    + p.sm.toFixed(3)
    + "</div>"
    + "</div>";


  det += "</div>";


  det +=
    "<div class='pdaltitle sr d3'>"
    + "ALERTES ASSOCIEES — "
    + als.length
    + "</div>";


  det +=
    "<div class='sr d4'>"
    + alh
    + "</div>"
    + "</div>";


  document.getElementById("pdetail").innerHTML =
    det;


  var btnLocDetail = document.getElementById("btn-loc-detail");

  if (btnLocDetail) {

    btnLocDetail.addEventListener("click", function() {
      voirSurCarte(p.n);
    });

  }


  obs();
}


// ─────────────────────────────────────────────────────────────────────────────
// CARTE
// ─────────────────────────────────────────────────────────────────────────────

var mapBuilt = false;
var mapBlobUrl = null;


function buildCarte(force) {

  if (mapBuilt && !force) return;

  mapBuilt = true;


  // Nicads concernés par les alertes toutes fraîches (import / detection ML)
  var nicadsNouveaux = {};

  for (var k = 0; k < nouvellesAlertesIds.length; k++) {

    var id = nouvellesAlertesIds[k];

    var alerte = A.filter(function(x) {
      return x.id === id;
    })[0];

    if (alerte) {
      nicadsNouveaux[alerte.n] = true;
    }
  }


  var mk = "";
  var focusScript = "";
  var nbValides = 0;
  var nbInvalides = 0;

  // Coordonnees des parcelles concernees par les alertes toutes fraiches :
  // sert a zoomer automatiquement dessus et a compter le bandeau d'alerte.
  var coordsNouvelles = [];

  for (var i = 0; i < P.length; i++) {

    var p = P[i];

    var latOk = isFinite(p.lat) && p.lat !== 0;
    var lonOk = isFinite(p.lon) && p.lon !== 0;

    if (!latOk || !lonOk) {
      nbInvalides++;
      continue;
    }

    nbValides++;

    var lat = p.lat;
    var lon = p.lon;

    var col =
      scol(p.sm);

    var estNouveau = !!nicadsNouveaux[p.n];
    var estFocus = focusNicad && p.n === focusNicad;

    var r =
      estNouveau || estFocus
        ? 13
        : (p.na > 0 ? 10 : 7);


    var pop =
      "<b>"
      + esc(p.n)
      + "</b><br>"
      + esc(p.z)
      + "<br>"
      + p.na
      + " alerte(s)<br>"
      + "Score: "
      + p.sm.toFixed(3);


    mk +=
      "L.circleMarker(["
      + lat
      + ","
      + lon
      + "],{radius:"
      + r
      + ",color:"
      + JSON.stringify(col)
      + ",fillColor:"
      + JSON.stringify(col)
      + ",fillOpacity:0.85,weight:1.5})"
      + ".addTo(map)"
      + ".bindPopup("
      + JSON.stringify(pop)
      + ");";


    if (estNouveau) {

      coordsNouvelles.push([lat, lon]);

      // Halo cyan (couleur absente du reste de la palette) qui grossit
      // et retrecit en continu autour de la parcelle : c'est le repere
      // visuel "nouvelle alerte", impossible a confondre avec le reste.
      mk +=
        "var halo=L.circleMarker(["
        + lat
        + ","
        + lon
        + "],{radius:14,color:'#00C2FF',weight:3,"
        + "fillOpacity:0.15,fillColor:'#00C2FF'})"
        + ".addTo(map);"
        + "pulseHalos.push(halo);";
    }


    if (estFocus) {

      focusScript =
        "map.setView(["
        + lat
        + ","
        + lon
        + "],17);"
        + "var hl=L.circleMarker(["
        + lat
        + ","
        + lon
        + "],{radius:20,color:'#A82820',weight:3,"
        + "fillOpacity:0,className:'sdfcs-pulse'})"
        + ".addTo(map);"
        + "setTimeout(function(){"
        + "L.popup().setLatLng(["
        + lat
        + ","
        + lon
        + "]).setContent("
        + JSON.stringify(pop)
        + ").openOn(map);"
        + "},350);";
    }
  }


  var parts = [

    "<!DOCTYPE html><html><head>",

    "<link rel=\"stylesheet\" "
    + "href=\"https://unpkg.com/leaflet@1.9.4/"
    + "dist/leaflet.css\"/>",

    "<script src=\"https://unpkg.com/leaflet@1.9.4/"
    + "dist/leaflet.js\"><\/script>",

    "<style>"
    + "html,body,#map{"
    + "margin:0;padding:0;"
    + "width:100%;height:100vh;"
    + "}"
    + "#sdfcs-err{"
    + "position:absolute;top:0;left:0;right:0;z-index:99999;"
    + "background:#A82820;color:#fff;font:12px monospace;"
    + "padding:10px 14px;display:none;white-space:pre-wrap;"
    + "}"
    // Surbrillance "nouvelle alerte" / "parcelle ciblée" : anneau pulsant
    + ".sdfcs-pulse{animation:sdfcsPulse 1.1s ease-out infinite;}"
    + "@keyframes sdfcsPulse{"
    + "0%{stroke-opacity:1;filter:drop-shadow(0 0 4px currentColor);}"
    + "100%{stroke-opacity:.15;}"
    + "}"
    + "<\/style>"
    + "</head><body>",

    "<div id=\"sdfcs-err\"></div>",
    "<div id=\"map\"></div>",

    "<script>",

    "window.onerror=function(msg,src,line,col,err){"
    + "var e=document.getElementById(\"sdfcs-err\");"
    + "e.style.display=\"block\";"
    + "e.textContent=\"ERREUR CARTE (ligne \"+line+\"): \"+msg;"
    + "return false;"
    + "};",

    "try{",

    "var map=L.map(\"map\")"
    + ".setView([14.716,-17.467],13);",

    "L.tileLayer("
    + "\"https://server.arcgisonline.com/ArcGIS/rest/services/"
    + "World_Imagery/MapServer/tile/{z}/{y}/{x}\","
    + "{attribution:\"Tiles &copy; Esri\","
    + "maxZoom:19}"
    + ").addTo(map);",

    "var pulseHalos=[];",

    mk,

    focusScript,

    // Si un import/detection vient de creer des alertes : zoom
    // automatique dessus (sinon elles peuvent tomber hors du cadre
    // par defaut centre sur Dakar) + bandeau fixe impossible a rater.
    (coordsNouvelles.length > 0
      ? "map.fitBounds("
        + JSON.stringify(coordsNouvelles)
        + ",{padding:[60,60],maxZoom:16});"
      : ""),

    (coordsNouvelles.length > 0
      ? "var b=document.createElement('div');"
        + "b.style.cssText="
        + JSON.stringify(
            "position:absolute;top:12px;left:50%;"
            + "transform:translateX(-50%);"
            + "background:#00C2FF;color:#0A1A1F;"
            + "font:bold 13px monospace;padding:10px 20px;"
            + "border-radius:4px;z-index:9999;"
            + "box-shadow:0 2px 8px rgba(0,0,0,.35);"
          )
        + ";"
        + "b.textContent="
        + JSON.stringify(
            "\uD83C\uDD95 "
            + coordsNouvelles.length
            + " nouvelle(s) alerte(s) detectee(s)"
          )
        + ";"
        + "document.body.appendChild(b);"
      : ""),

    // Pulsation animee du halo (via JS plutot que CSS pur : plus fiable
    // sur les circleMarker SVG de Leaflet selon les navigateurs).
    "setInterval(function(){"
    + "var t=Date.now()/300;"
    + "var r=14+6*Math.abs(Math.sin(t));"
    + "pulseHalos.forEach(function(h){h.setRadius(r);});"
    + "},50);",

    "map.on(\"mousemove\",function(e){"
    + "window.parent.postMessage({"
    + "lat:e.latlng.lat.toFixed(5),"
    + "lon:e.latlng.lng.toFixed(5),"
    + "zoom:map.getZoom()"
    + "},\"*\");"
    + "});",

    "}catch(e){"
    + "var el=document.getElementById(\"sdfcs-err\");"
    + "el.style.display=\"block\";"
    + "el.textContent=\"ERREUR CARTE: \"+e.message;"
    + "}",

    "<\/script></body></html>"

  ];


  var blob =
    new Blob(
      [parts.join("")],
      {
        type: "text/html"
      }
    );


  if (mapBlobUrl) {
    URL.revokeObjectURL(mapBlobUrl);
  }

  mapBlobUrl = URL.createObjectURL(blob);

  document.getElementById(
    "mapframe"
  ).src =
    mapBlobUrl;


  // Le focus et les surbrillances "nouvelles alertes" ne servent qu'une
  // fois : on les consomme puis on les vide pour ne pas les rejouer au
  // prochain rebuild automatique (ex: après un simple loadAll()).
  focusNicad = null;
  nouvellesAlertesIds = [];
}


window.addEventListener(
  "message",
  function(e) {

    if (e.data && e.data.lat) {

      document.getElementById(
        "ccoord"
      ).textContent =
        "LAT "
        + e.data.lat
        + " LON "
        + e.data.lon
        + " ZOOM "
        + e.data.zoom;
    }

  }
);


// ─────────────────────────────────────────────────────────────────────────────
// IMPORT
// ─────────────────────────────────────────────────────────────────────────────

function setupDrop(
  dropId,
  inputId,
  filenameId
) {

  var drop =
    document.getElementById(dropId);

  var input =
    document.getElementById(inputId);

  var fn =
    document.getElementById(filenameId);


  input.addEventListener(
    "change",
    function() {

      if (this.files[0]) {

        fn.textContent =
          this.files[0].name;
      }

    }
  );


  drop.addEventListener(
    "dragover",
    function(e) {

      e.preventDefault();

      this.classList.add(
        "dragover"
      );

    }
  );


  drop.addEventListener(
    "dragleave",
    function() {

      this.classList.remove(
        "dragover"
      );

    }
  );


  drop.addEventListener(
    "drop",
    function(e) {

      e.preventDefault();

      this.classList.remove(
        "dragover"
      );


      var f =
        e.dataTransfer.files[0];


      if (f) {

        input.files =
          e.dataTransfer.files;

        fn.textContent =
          f.name;
      }

    }
  );
}


setupDrop(
  "drop-shp",
  "input-shp",
  "fn-shp"
);


setupDrop(
  "drop-csv",
  "input-csv",
  "fn-csv"
);


// ─────────────────────────────────────────────────────────────────────────────
// OUTIL COMMUN : capturer les ids d'alertes avant/après une action, pour
// savoir lesquelles sont "nouvelles" et les mettre en évidence sur la carte.
// ─────────────────────────────────────────────────────────────────────────────

function idsAlertesActuelles() {

  return request("/alertes")
    .then(function(r) {
      return r.json();
    })
    .then(function(alertes) {
      return alertes.map(function(a) {
        return a.id_alerte;
      });
    });
}


function apresGenerationAlertes(idsAvant) {

  return idsAlertesActuelles().then(function(idsApres) {

    nouvellesAlertesIds = idsApres.filter(function(id) {
      return idsAvant.indexOf(id) === -1;
    });

    loadAll();
    buildHistorique();

    if (nouvellesAlertesIds.length > 0) {

      // Visibilité immédiate : on bascule direct sur la carte et les
      // nouvelles alertes y apparaissent en surbrillance pulsante,
      // sans que l'utilisateur ait besoin d'aller vérifier le tableau.
      goSection("carte");
      buildCarte(true);
    }

    return idsApres.length - idsAvant.length;
  });
}


// ─────────────────────────────────────────────────────────────────────────────
// SHAPEFILE
// ─────────────────────────────────────────────────────────────────────────────

document
  .getElementById("btn-shp")
  .addEventListener(
    "click",
    function() {

      var file =
        document.getElementById(
          "input-shp"
        ).files[0];


      if (!file) {

        showMsg(
          "msg-shp",
          "Aucun fichier selectionne",
          "err"
        );

        return;
      }


      showMsg(
        "msg-shp",
        "Import en cours...",
        "info"
      );


      var idsAvant = [];
      var nbImporteesGlobal = 0;


      idsAlertesActuelles()

        .then(function(ids) {

          idsAvant = ids;

          var fd =
            new FormData();

          fd.append(
            "file",
            file
          );


          return request(
            "/import/shapefile",
            {
              method: "POST",
              body: fd
            }
          );

        })


        .then(function(r) {
          return r.json();
        })


        .then(function(data) {

          showMsg(
            "msg-shp",
            data.message,
            "ok"
          );

          nbImporteesGlobal = data.inserees;

          return apresGenerationAlertes(idsAvant);

        })


        .then(function(nouv) {

          showComparaison(
            nbImporteesGlobal,
            nouv
          );

        })


        .catch(function() {

          showMsg(
            "msg-shp",
            "Erreur lors de l import",
            "err"
          );

        });

    }
  );


// ─────────────────────────────────────────────────────────────────────────────
// CSV
// ─────────────────────────────────────────────────────────────────────────────

document
  .getElementById("btn-csv")
  .addEventListener(
    "click",
    function() {

      var file =
        document.getElementById(
          "input-csv"
        ).files[0];


      var type =
        document.getElementById(
          "select-csv-type"
        ).value;


      if (!file) {

        showMsg(
          "msg-csv",
          "Aucun fichier selectionne",
          "err"
        );

        return;
      }


      showMsg(
        "msg-csv",
        "Import en cours...",
        "info"
      );


      var idsAvant = [];

      var fd =
        new FormData();


      fd.append(
        "file",
        file
      );


      idsAlertesActuelles()

        .then(function(ids) {

          idsAvant = ids;

          return request(
            "/import/csv?type_donnee="
            + type,
            {
              method: "POST",
              body: fd
            }
          );

        })

        .then(function(r) {
          return r.json();
        })

        .then(function(data) {

          showMsg(
            "msg-csv",
            data.message,
            "ok"
          );

          // Un import CSV de transactions peut déclencher le trigger
          // "zone_illegale" côté PostGIS : on vérifie donc aussi les
          // nouvelles alertes ici, pas seulement après la détection ML.
          return apresGenerationAlertes(idsAvant);

        })

        .catch(function() {

          showMsg(
            "msg-csv",
            "Erreur lors de l import",
            "err"
          );

        });

    }
  );


// ─────────────────────────────────────────────────────────────────────────────
// DETECTION ML
// ─────────────────────────────────────────────────────────────────────────────

document
  .getElementById("btn-detection")
  .addEventListener(
    "click",
    function() {

      showMsg(
        "msg-detect",
        "Detection ML en cours...",
        "info"
      );


      var idsAvant = [];


      idsAlertesActuelles()

        .then(function(ids) {

          idsAvant = ids;

          return request(
            "/detection/run",
            {
              method: "POST"
            }
          );

        })

        .then(function(r) {
          return r.json();
        })

        .then(function(data) {

          showMsg(
            "msg-detect",
            data.message,
            "ok"
          );

          return apresGenerationAlertes(idsAvant);

        })

        .catch(function() {

          showMsg(
            "msg-detect",
            "Erreur detection",
            "err"
          );

        });

    }
  );


// ─────────────────────────────────────────────────────────────────────────────
// COMPARAISON (bloc affiché après un import shapefile)
// ─────────────────────────────────────────────────────────────────────────────

function showComparaison(
  nbImp,
  nouv
) {

  var taux =
    nbImp > 0
      ? Math.round(
          (nouv / nbImp) * 100
        )
      : 0;


  document.getElementById(
    "cmp-imp"
  ).textContent =
    nbImp;


  document.getElementById(
    "cmp-al"
  ).textContent =
    nouv > 0
      ? "+" + nouv
      : "0";


  document.getElementById(
    "cmp-tx"
  ).textContent =
    taux + "%";


  document.getElementById(
    "compare-block"
  ).classList.add("show");

}


// ─────────────────────────────────────────────────────────────────────────────
// HISTORIQUE
// ─────────────────────────────────────────────────────────────────────────────

function buildHistorique() {

  request("/alertes")

    .then(function(r) {
      return r.json();
    })

    .then(function(alertes) {

      document.getElementById(
        "hist-total"
      ).textContent =
        pad(alertes.length, 3);


      var byDate = {};


      for (
        var i = 0;
        i < alertes.length;
        i++
      ) {

        var a = alertes[i];


        var dt =
          a.date_detection
            ? a.date_detection.substring(
                0,
                10
              )
            : "Inconnu";


        if (!byDate[dt]) {
          byDate[dt] = [];
        }


        byDate[dt].push(a);
      }


      var dates =
        Object.keys(byDate)
          .sort()
          .reverse();


      var html = "";


      for (
        var d = 0;
        d < dates.length;
        d++
      ) {

        var date =
          dates[d];

        var items =
          byDate[date];


        html +=
          "<div class='hist-day sr' "
          + "style='transition-delay:"
          + (d * 0.07)
          + "s'>";


        html +=
          "<div class='hist-day-label'>"
          + esc(date)
          + " — "
          + items.length
          + " alerte(s)"
          + "</div>";


        for (
          var i = 0;
          i < items.length;
          i++
        ) {

          var a =
            items[i];


          var cl =
            a.score_risque >= 0.8
              ? "r"
              : a.score_risque >= 0.5
                ? "g"
                : "n";


          var heure =
            a.date_detection &&
            a.date_detection.length > 10
              ? a.date_detection.substring(
                  11,
                  16
                )
              : "--";


          var confHtml = "";


          if (
            a.statut === "resolue"
          ) {

            confHtml =
              "<span class='tag-ok'>"
              + "CONFIRME"
              + "</span>";

          }

          else if (
            a.statut === "fausse_alerte"
          ) {

            confHtml =
              "<span class='tag-ko'>"
              + "REJETE"
              + "</span>";

          }

          else {

            confHtml =
              "<div class='conf-wrap'>";


            confHtml +=
              "<button class='cbtn ok' "
              + "data-id='"
              + a.id_alerte
              + "' "
              + "data-st='resolue'>"
              + "Confirmer"
              + "</button>";


            confHtml +=
              "<button class='cbtn ko' "
              + "data-id='"
              + a.id_alerte
              + "' "
              + "data-st='fausse_alerte'>"
              + "Rejeter"
              + "</button>";


            confHtml +=
              "</div>";
          }

          confHtml +=
            "<button class='cbtn loc' data-nicad='"
            + esc(a.nicad)
            + "'>Localiser</button>";


          html +=
            "<div class='hist-item'>";


          html +=
            "<div class='hist-heure'>"
            + esc(heure)
            + "</div>";


          html +=
            "<div style='flex:1'>";


          html +=
            "<div style='display:flex;"
            + "align-items:center;"
            + "gap:10px;"
            + "margin-bottom:3px'>";


          html +=
            "<span class='abadge "
            + cl
            + "'>"
            + esc(
                a.type_anomalie
                  .replace(
                    /_/g,
                    " "
                  )
                  .toUpperCase()
              )
            + "</span>";


          html +=
            "<span style='font-family:"
            + "IBM Plex Mono,monospace;"
            + "font-size:12px;"
            + "font-weight:500'>"
            + esc(a.nicad)
            + "</span>";


          html +=
            "</div>";


          html +=
            "<div style='font-size:11px;"
            + "color:#5A4E38'>"
            + esc(a.description)
            + "</div>";


          html +=
            "</div>";


          html +=
            "<div style='font-family:"
            + "monospace;"
            + "font-size:18px;"
            + "color:"
            + scol(a.score_risque)
            + ";text-align:right'>"
            + parseFloat(
                a.score_risque
              ).toFixed(3)
            + "</div>";


          html +=
            confHtml;


          html +=
            "</div>";
        }


        html +=
          "</div>";
      }


      document.getElementById(
        "hist-list"
      ).innerHTML =
        html;


      document
        .querySelectorAll(".hist-list .cbtn.ok, .hist-list .cbtn.ko")
        .forEach(function(btn) {

          btn.addEventListener(
            "click",
            function() {

              confirmerAlerte(
                parseInt(
                  this.getAttribute(
                    "data-id"
                  )
                ),

                this.getAttribute(
                  "data-st"
                )
              );

            }
          );

        });


      document
        .querySelectorAll(".hist-list .cbtn.loc")
        .forEach(function(btn) {

          btn.addEventListener(
            "click",
            function() {

              voirSurCarte(
                this.getAttribute("data-nicad")
              );

            }
          );

        });


      obs();

    });
}


// ─────────────────────────────────────────────────────────────────────────────
// MENU MOBILE (tiroir d'onglets)
// ─────────────────────────────────────────────────────────────────────────────

var menuToggle = document.getElementById("menu-toggle");
var tabsEl = document.getElementById("tabs");
var scrimEl = document.getElementById("tabs-scrim");
var currentTabEl = document.getElementById("current-tab");

function setMenu(open) {
  tabsEl.classList.toggle("open", open);
  scrimEl.classList.toggle("show", open);
}

if (menuToggle) {
  menuToggle.addEventListener("click", function() {
    setMenu(!tabsEl.classList.contains("open"));
  });
}

if (scrimEl) {
  scrimEl.addEventListener("click", function() {
    setMenu(false);
  });
}


// ─────────────────────────────────────────────────────────────────────────────
// NAVIGATION
// ─────────────────────────────────────────────────────────────────────────────

function goSection(id) {

  document
    .querySelectorAll("section")
    .forEach(function(s) {
      s.classList.remove("on");
    });


  document
    .querySelectorAll(".tab")
    .forEach(function(t) {
      t.classList.remove("on");
    });


  document
    .getElementById(id)
    .classList.add("on");


  document
    .getElementById(
      "tab-" + id
    )
    .classList.add("on");


  if (id === "carte") {
    buildCarte();
  }


  if (id === "historique") {
    buildHistorique();
  }


  if (currentTabEl) {
    currentTabEl.textContent =
      document.getElementById("tab-" + id).textContent;
  }

  setMenu(false);


  window.scrollTo(
    0,
    0
  );


  obs();
}


document
  .getElementById("tab-vue")
  .addEventListener(
    "click",
    function() {
      goSection("vue");
    }
  );


document
  .getElementById("tab-carte")
  .addEventListener(
    "click",
    function() {
      goSection("carte");
    }
  );


document
  .getElementById("tab-alertes")
  .addEventListener(
    "click",
    function() {
      goSection("alertes");
    }
  );


document
  .getElementById("tab-parcelles")
  .addEventListener(
    "click",
    function() {
      goSection("parcelles");
    }
  );


document
  .getElementById("tab-import")
  .addEventListener(
    "click",
    function() {
      goSection("import");
    }
  );


document
  .getElementById("tab-historique")
  .addEventListener(
    "click",
    function() {
      goSection("historique");
    }
  );


// ─────────────────────────────────────────────────────────────────────────────
// SCROLL OBSERVER
// ─────────────────────────────────────────────────────────────────────────────

var io =
  new IntersectionObserver(
    function(entries) {

      for (
        var i = 0;
        i < entries.length;
        i++
      ) {

        if (
          entries[i].isIntersecting
        ) {

          entries[i]
            .target
            .classList
            .add("v");


          io.unobserve(
            entries[i].target
          );
        }
      }

    },
    {
      threshold: 0.1
    }
  );


function obs() {

  requestAnimationFrame(
    function() {

      document
        .querySelectorAll(
          ".sr:not(.v)"
        )
        .forEach(
          function(el) {
            io.observe(el);
          }
        );

    }
  );
}


// ─────────────────────────────────────────────────────────────────────────────
// HORLOGE
// ─────────────────────────────────────────────────────────────────────────────

function tick() {

  var n =
    new Date();


  document.getElementById(
    "clk"
  ).textContent =

    String(
      n.getHours()
    ).padStart(2, "0")

    + ":"

    + String(
        n.getMinutes()
      ).padStart(2, "0")

    + ":"

    + String(
        n.getSeconds()
      ).padStart(2, "0");
}


setInterval(
  tick,
  1000
);

tick();


// ─────────────────────────────────────────────────────────────────────────────
// INIT
// ─────────────────────────────────────────────────────────────────────────────

request("/stats")

  .then(function(r) {
    return r.json();
  })

  .then(function() {

    hideLoginOverlay();

    loadAll();
    obs();

  })

  .catch(function() {

    // Pas de session valide : l'overlay reste affiche,
    // en attente de la soumission du formulaire.

  });