# Copyright (c) 2019 Ultimaker B.V.
# Cura is released under the terms of the LGPLv3 or higher.
from collections import deque
import os
from typing import TYPE_CHECKING, Optional, List, Dict, Any

from PyQt5.QtCore import QUrl, Qt, pyqtSlot, pyqtProperty, pyqtSignal

from UM.Logger import Logger
from UM.Qt.ListModel import ListModel
from UM.Resources import Resources

if TYPE_CHECKING:
    from PyQt5.QtCore import QObject
    from cura.CuraApplication import CuraApplication


#
# This is the Qt ListModel that contains all welcome pages data. Each page is a page that can be shown as a step in the
# welcome wizard dialog. Each item in this ListModel represents a page, which contains the following fields:
#
#  - id           : A unique page_id which can be used in function goToPage(page_id)
#  - page_url     : The QUrl to the QML file that contains the content of this page
#  - next_page_id : (OPTIONAL) The next page ID to go to when this page finished. This is optional. If this is not
#                   provided, it will go to the page with the current index + 1
#  - should_show_function : (OPTIONAL) An optional function that returns True/False indicating if this page should be
#                           shown. By default all pages should be shown. If a function returns False, that page will
#                           be skipped and its next page will be shown.
#
# Note that in any case, a page that has its "should_show_function" == False will ALWAYS be skipped.
#
class WelcomePagesModel(ListModel):

    IdRole = Qt.UserRole + 1  # Page ID
    PageUrlRole = Qt.UserRole + 2  # URL to the page's QML file
    NextPageIdRole = Qt.UserRole + 3  # The next page ID it should go to

    def __init__(self, application: "CuraApplication", parent: Optional["QObject"] = None) -> None:
        super().__init__(parent)

        self.addRoleName(self.IdRole, "id")
        self.addRoleName(self.PageUrlRole, "page_url")
        self.addRoleName(self.NextPageIdRole, "next_page_id")

        self._application = application

        self._pages = []  # type: List[Dict[str, Any]]

        self._current_page_index = 0
        # Store all the previous page indices so it can go back.
        self._previous_page_indices_stack = deque()  # type: deque

    allFinished = pyqtSignal()  # emitted when all steps have been finished
    currentPageIndexChanged = pyqtSignal()

    @pyqtProperty(int, notify = currentPageIndexChanged)
    def currentPageIndex(self) -> int:
        return self._current_page_index

    # Returns a float number in [0, 1] which indicates the current progress.
    @pyqtProperty(float, notify = currentPageIndexChanged)
    def currentProgress(self) -> float:
        if len(self._items) == 0:
            return 0
        else:
            return self._current_page_index / len(self._items)

    # Indicates if the current page is the last page.
    @pyqtProperty(bool, notify = currentPageIndexChanged)
    def isCurrentPageLast(self) -> bool:
        return self._current_page_index == len(self._items) - 1

    def _setCurrentPageIndex(self, page_index: int) -> None:
        if page_index != self._current_page_index:
            self._previous_page_indices_stack.append(self._current_page_index)
            self._current_page_index = page_index
            self.currentPageIndexChanged.emit()

    # Ends the Welcome-Pages. Put as a separate function for cases like the 'decline' in the User-Agreement.
    @pyqtSlot()
    def atEnd(self) -> None:
        self.allFinished.emit()
        self.resetState()

    # Goes to the next page.
    # If "from_index" is given, it will look for the next page to show starting from the "from_index" page instead of
    # the "self._current_page_index".
    @pyqtSlot()
    def goToNextPage(self, from_index: Optional[int] = None) -> None:
        # Look for the next page that should be shown
        current_index = self._current_page_index if from_index is None else from_index
        while True:
            page_item = self._items[current_index]

            # Check if there's a "next_page_id" assigned. If so, go to that page. Otherwise, go to the page with the
            # current index + 1.
            next_page_id = page_item.get("next_page_id")
            next_page_index = current_index + 1
            if next_page_id:
                idx = self.getPageIndexById(next_page_id)
                if idx is None:
                    # FIXME: If we cannot find the next page, we cannot do anything here.
                    Logger.log("e", "Cannot find page with ID [%s]", next_page_id)
                    return
                next_page_index = idx

            # If we have reached the last page, emit allFinished signal and reset.
            if next_page_index == len(self._items):
                self.atEnd()
                return

            # Check if the this page should be shown (default yes), if not, keep looking for the next one.
            next_page_item = self.getItem(next_page_index)
            if self._shouldPageBeShown(next_page_index):
                break

            Logger.log("d", "Page [%s] should not be displayed, look for the next page.", next_page_item["id"])
            current_index = next_page_index

        # Move to the next page
        self._setCurrentPageIndex(next_page_index)

    # Goes to the previous page. If there's no previous page, do nothing.
    @pyqtSlot()
    def goToPreviousPage(self) -> None:
        if len(self._previous_page_indices_stack) == 0:
            Logger.log("i", "No previous page, do nothing")
            return

        previous_page_index = self._previous_page_indices_stack.pop()
        self._current_page_index = previous_page_index
        self.currentPageIndexChanged.emit()

    # Sets the current page to the given page ID. If the page ID is not found, do nothing.
    @pyqtSlot(str)
    def goToPage(self, page_id: str) -> None:
        page_index = self.getPageIndexById(page_id)
        if page_index is None:
            # FIXME: If we cannot find the next page, we cannot do anything here.
            Logger.log("e", "Cannot find page with ID [%s]", page_index)
            return

        if self._shouldPageBeShown(page_index):
            # Move to that page if it should be shown
            self._setCurrentPageIndex(page_index)
        else:
            # Find the next page to show starting from the "page_index"
            self.goToNextPage(from_index = page_index)

    # Checks if the page with the given index should be shown by calling the "should_show_function" associated with it.
    # If the function is not present, returns True (show page by default).
    def _shouldPageBeShown(self, page_index: int) -> bool:
        next_page_item = self.getItem(page_index)
        should_show_function = next_page_item.get("should_show_function", lambda: True)
        return should_show_function()

    # Resets the state of the WelcomePagesModel. This functions does the following:
    #  - Resets current_page_index to 0
    #  - Clears the previous page indices stack
    @pyqtSlot()
    def resetState(self) -> None:
        self._current_page_index = 0
        self._previous_page_indices_stack.clear()

        self.currentPageIndexChanged.emit()

    # Gets the page index with the given page ID. If the page ID doesn't exist, returns None.
    def getPageIndexById(self, page_id: str) -> Optional[int]:
        page_idx = None
        for idx, page_item in enumerate(self._items):
            if page_item["id"] == page_id:
                page_idx = idx
                break
        return page_idx

    # Convenience function to get QUrl path to pages that's located in "resources/qml/WelcomePages".
    def _getBuiltinWelcomePagePath(self, page_filename: str) -> "QUrl":
        from cura.CuraApplication import CuraApplication
        return QUrl.fromLocalFile(Resources.getPath(CuraApplication.ResourceTypes.QmlFiles,
                                                    os.path.join("WelcomePages", page_filename)))

    def initialize(self) -> None:
        # Add default welcome pages
        self._pages.append({"id": "welcome",
                            "page_url": self._getBuiltinWelcomePagePath("WelcomeContent.qml"),
                            })
        self._pages.append({"id": "user_agreement",
                            "page_url": self._getBuiltinWelcomePagePath("UserAgreementContent.qml"),
                            })
        self._pages.append({"id": "whats_new",
                            "page_url": self._getBuiltinWelcomePagePath("WhatsNewContent.qml"),
                            })
        self._pages.append({"id": "data_collections",
                            "page_url": self._getBuiltinWelcomePagePath("DataCollectionsContent.qml"),
                            })
        self._pages.append({"id": "add_network_or_local_printer",
                            "page_url": self._getBuiltinWelcomePagePath("AddNetworkOrLocalPrinterContent.qml"),
                            "next_page_id": "machine_actions",
                            })
        self._pages.append({"id": "add_printer_by_ip",
                            "page_url": self._getBuiltinWelcomePagePath("AddPrinterByIpContent.qml"),
                            "next_page_id": "machine_actions",
                            })
        self._pages.append({"id": "machine_actions",
                            "page_url": self._getBuiltinWelcomePagePath("FirstStartMachineActionsContent.qml"),
                            "next_page_id": "cloud",
                            "should_show_function": self.shouldShowMachineActions,
                            })
        self._pages.append({"id": "cloud",
                            "page_url": self._getBuiltinWelcomePagePath("CloudContent.qml"),
                            })

        self.setItems(self._pages)

    # Indicates if the machine action panel should be shown by checking if there's any first start machine actions
    # available.
    def shouldShowMachineActions(self) -> bool:
        global_stack = self._application.getMachineManager().activeMachine
        if global_stack is None:
            return False

        definition_id = global_stack.definition.getId()
        first_start_actions = self._application.getMachineActionManager().getFirstStartActions(definition_id)
        return len([action for action in first_start_actions if action.needsUserInteraction()]) > 0

    def addPage(self) -> None:
        pass


__all__ = ["WelcomePagesModel"]