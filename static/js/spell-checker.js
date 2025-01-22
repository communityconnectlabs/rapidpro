function spellCheckerFunc(text) {
  return new Promise((resolve, reject) => {
    const myHeaders = new Headers();
    myHeaders.append("Content-Type", "application/x-www-form-urlencoded");

    const urlencoded = new URLSearchParams();
    urlencoded.append("language", "en-US");
    urlencoded.append("text", text);

    const requestOptions = {
      method: "POST",
      headers: myHeaders,
      body: urlencoded,
      redirect: "follow"
    };

    fetch("https://language-tool.communityconnectlabs.com/v2/check", requestOptions)
      .then((response) => response.json())
      .then((result) => {
        resolve(result.matches.map(match => {
          return {
            message: match.message,
            from: match.offset,
            to: match.offset + match.length,
            suggestions: match.replacements.map(repl => repl.value),
          };
        }));
      })
      .catch((error) => reject(error));
  });
}

function correctPositionOfSpellChecker(parent, checker) {
  checker.addEventListener("temba-spell-corrections-found", (evt) => {
    setTimeout(() => {
      let corrections = checker.shadowRoot.querySelectorAll(".spell-correction");
      corrections.forEach(correction => {
        correction.onmouseenter = (e) => {
          e.preventDefault();
          let tooltip = correction.querySelector(".tooltip");
          let tooltipRect = tooltip.getBoundingClientRect();
          let parentRect = parent.getBoundingClientRect();
          if (Array.from(tooltip.classList).some(c => ["top", "bottom", "left", "right"].includes(c))) return;
          if (parentRect.x > tooltipRect.x + 10) {
            tooltip.classList.add("right");
          } else if (parentRect.x + parentRect.width < tooltipRect.x + tooltipRect.width + 10) {
            tooltip.classList.add("left");
          } else if (parentRect.y > tooltipRect.y + 10) {
            tooltip.classList.add("bottom");
          } else {
            tooltip.classList.add("top");
          }
        };
      });
    }, 10);
  });
}