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
