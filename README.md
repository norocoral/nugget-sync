<img width="1920" height="1080" alt="Mar" src="https://github.com/user-attachments/assets/0151f3ad-b7b4-49ea-86a9-85f431f288d8" />

---

> [!NOTE]
> Only macOS is supported. I have no plans to make a Windows or Linux version

### 0. About me
Nugget Sync uses your local Apple Music library and allows it to be synced to any device or folder. It supports bi-directional changes, favorites syncing, AAC conversion, selection of library to be synced, playlists syncing and Rockbox specific feature

### 1. Select a drive
Go to File > Change Destination…
Nugget Sync will remember this destination on the next app launch.

### 3. Change settings
Go to Nugget Sync > Settings…
Nugget Sync will remember settings for this destination.

### 4. Sync to destination
Click ‘Do it.’ on the main UI screen. It will sync shortly.
Please ensure to allow any permissions, such as your Music library and access to removable drives.
   
### 6. Upon completion
Once the sync is completed, you can safely eject. Nugget Sync can do it from the UI if you wish.

> [!CAUTION]
> A .NUGLIB file will be created in the destination device. **DO NOT DELETE IT.**
> It manages the sync settings for the device and the internal library. If deleted, sync will run slower and potential data loss will occur.

---

# Demo

<img width="674" height="254" alt="1456" src="https://github.com/user-attachments/assets/ddedf7ea-ee23-453d-b0fe-a000a3e56503" />

---

# Dependencies
Not required to install on the .app file, but to build and run the script

* macOS Music.app on Sonoma or up (Have not tested on older versions)
* Python >=3.8
* PyInstaller >=6.0
* mutagen >=1.47

---

<img width="16" height="16" alt="AppIcon16" src="https://github.com/user-attachments/assets/20c8154e-3125-4f8b-ba68-a51a3708bbfd"/>
Nugget Sync, MIT license
