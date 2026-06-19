I am in a Telegram group where a person has a team of analysts who analyze the financial market, specifically the forex market, because only forex trades are discussed. When they have a signal ready for me to copy trade, they send it in the Telegram group. Messages come only from the admin side; as a member, I cannot send messages. Below is the format for the two different types of signals: a put signal for selling and a call signal for buying:

Put:
 
💰5-minute expiration
EUR/JPY;10:20;PUT🟥

🕛TIME UNTIL 10:25

1st GALE ->TIME UNTIL 10:30
2nd GALE - TIME UNTIL 10:35

📲 Click to open the broker

🙋‍♂️ Don't know how to trade yet? Click here

2. Call: 

💰5-minute expiration
USD/EGP;20:00;CALL🟩

🕛TIME UNTIL 20:05

1st GALE ->TIME UNTIL 20:10
2nd GALE - TIME UNTIL 20:15

📲 Click to open the broker

🙋‍♂️ Don't know how to trade yet? Click here

Now, to explain how this signal works: the first header mentions that we're doing a five‑minute expiration, so the trade must only last for five minutes. The next line contains the most important part of the signal, which is the currency pair we are trading. In the put example, it is Euro versus Japanese yen And then after the semicolon is the time that this trade must be placed, which is, in this case, 10:20. After the next semicolon is the direction of the trade, which is a put, so it’s going to be a sell. Below that is the time until, which is 10:25. As we saw in the first line, that means a five‑minute expiration, so that’s why it’s until 10:25—the total duration is five minutes. Then, if the trade unfortunately becomes a loss, we try to go on to the first gale. For the initial trade, which was supposed to be placed at 10:20 until 10:25, we put in $2. For the first gale we must put in double that amount, $4, to try to recover all losses. And then similarly, if our first gamble unfortunately results in a loss, we triple the amount this time. So we add another 4 dollars to make it 8 dollars, and we put in 8 dollars For the trade, we basically bet triple the amount we originally put in, but only if the first gale results in a loss. After that we don't go further; we don't try for a third gale. For both the first and second gale, we check whether the previous trade was a profit or a loss.

Let's say we place the trade from 10:20 to 10:25 and it becomes a loss. Then we place the first gale trade from 10:25 to 10:30. If that also becomes a loss, we place the second gale trade from 10:30 to 10:35. If the second gale also results in a loss, we stop and do not place another trade. The moment we hit a profit at any point, we stop.

So if we hit a profit on the initial 10:20‑10:25 trade, we would not do the first or second gale. If we don't hit a profit, we do the first gale; if we hit a profit there, we stop. If we still don't hit a profit, we do the second gale; if we hit a profit—or even a loss—we stop.

The same process applies to the call signal, which is a buy signal and is shown with a green block emoji. It follows the opposite direction but otherwise uses the same steps.

I hope that clarifies how the trades work.

Now, coming to the point of what I'm looking for right now: I want you to create a tool that will use my Telegram credentials to connect to my own Telegram account and constantly monitor a specific Telegram channel. It should examine every incoming message, and if the message matches the trade‑signal format—either a put or a call—it should extract the details: the expiration (e.g., five minutes), the currency pair, the time to place the trade, the direction (put or call), the first gale, the second gale, and any other relevant information. Using the API, it should place the trade at the correct moment. For example, with a put trade at 10:20, the tool should place the trade at 10:20 for a five‑minute expiry and all associated parameters, then continuously monitor the trade. If the trade results in a loss, it should execute the first gale and monitor that; if the first gale also results in a loss, it should execute the second gale and monitor that as well. This is what I am expecting from the tool.
