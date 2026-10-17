# Easy steps for noobies

### You must be here holding a .pkg file of a PS5 game and wondering "hmm, how do I play this game on my windows machine". You are in the right place, follow along.

1. Extract the `.pkg` to a folder. This folder must contain a file named `eboot.bin` and two folders `sce_module/`, `sce_sys/`.

<details>
<summary>There are multiple ways you can extract a .pkg to a folder. I will not list them here, a simple google search should help you.</summary>
Well, <a href=https://github.com/pearlxcore/PS5PKGTool>PS5PKGTOOL</a> <a href=https://github.com/thanhsondev/PSVIETHOA-FPKG-Builder>PSVIETHOA-FPKG-Builder</a> <a href=https://github.com/knutwurst/ps5-ultrapack>ps5-ultrapack</a>
</details>
<br>

2. [Download the GUI](https://github.com/biologyscience/ps5-exe-toolkit/releases/latest). Simply download the `.exe` and open it after placing it in a dedicated folder.

On first launch, it will download some required tools from [AnyPS5](https://github.com/boykopovar/AnyPS5). Keep the app open until the download finishes.

3. Choose the extracted .pkg **folder** in the GUI.

If you happen to see any red colored text on the left, that means the folder you selected does not have those files/folders. Once all of them are green colored, go to the next step.

4. Click **unSELF** to unwrapp SELF contained files.

This step is optional but highly recommended, especially if you have a fpkg file.

5. Then click **Build .exe** to generate the game which windows can understand.

This takes a bit of time according to the size of the game. Be paitent.

6. Open the `game.exe` located at the folder you selected in step 3.

7. Enjoy 🥳

If you face any issues, you know where to [go](https://github.com/biologyscience/ps5-exe-toolkit/issues). But before that, try reading the [full README](README.md) which might give you some answers.