#!/bin/bash


# Key Paramaters
DIR=$PWD
BIN="python3"
HELP_DISPLAY=0
SCRIPT_NAME=$(basename $0)


# Parsing Runtime Arguments
while getopts d:b:h flag
do
    case "${flag}" in
        d) DIR=${OPTARG};;
        b) BIN=${OPTARG};;
        h) HELP_DISPLAY=1;; 
    esac
done


# Help Screen Display Based on Runtime Argument
if [ $HELP_DISPLAY -eq 1 ]
then
    echo "======================================================================"
    echo "----------------------------------------------------------------------"
    echo "[$SCRIPT_NAME] Python Virtual Environment Set-Up with Jupyter Support"
    echo "----------------------------------------------------------------------"
    echo ""
    echo "Author: Abdullah Al Rashid (alrashid.abdullah@gmail.com)"
    echo ""
    echo "Date Started: 2022.06.08.W"
    echo "Last Updated: 2023.10.24.T"
    echo ""
    echo "-----------"
    echo "Usage Guide"
    echo "-----------"
    echo ""
    echo "Displaying the current help screen"
    echo " $ bash $SCRIPT_NAME -h"
    echo ""
    echo "Setting up virtual environment at ./venv under current directory "
    echo "using the system default binary $BIN"
    echo ""
    echo " $ bash $SCRIPT_NAME"
    echo ""
    echo "Setting up virtual environment at <directory>/venv using the system "
    echo "default binary $BIN"
    echo ""
    echo " $ bash $SCRIPT_NAME -d <directory>"
    echo ""
    echo ""
    echo "Setting up virtual environment at ./venv under current directory "
    echo "using a specific Python binary (Example: <python_binary_name> = "
    echo "python3.9)"
    echo ""
    echo " $ bash $SCRIPT_NAME -b <python_binary_name>"
    echo ""
    echo "Setting up virtual environment at <directory>/venv using a specific "
    echo "Python binary (Example: <python_binary_name> = python3.9)"
    echo ""
    echo " $ bash $SCRIPT_NAME -d <directory> -b <python_binary_name>"
    echo ""
    echo "(C) 2023 SavVant, Inc."
    echo "======================================================================"
    exit 0
fi


# Checking for Dependencies
echo "# Information [$SCRIPT_NAME]: Checking dependencies ..."
if [ $(which $BIN | wc -l) -lt 1 ]
then
    echo "! Error [$SCRIPT_NAME]: Unavaiable Python executable $BIN "
    echo "(installation needed)"
    exit 1
fi
if [ $($BIN -m pip -V | wc -l) -lt 1 ]
then
    echo "! Error [$SCRIPT_NAME]: Unmet dependency pip ($BIN module)"
    exit 1
fi
echo "Done"
echo ""


# Python Virrtual Environment Set-Up
echo "# Information [$SCRIPT_NAME]: Setting up Python3 virtual environment..."
if [ -z $DIR ]
then
    echo "! Error [$SCRIPT_NAME]: No directory specified (cf. runtime flag -d)"
    exit 1
elif [ ! -d "$DIR" ]
then
    echo "! Error [$SCRIPT_NAME]: Non-existent directory ($DIR) specified"
    exit 1
else
    echo "$ cd $DIR"
    cd $DIR
    if [ -d ./venv ]
    then
        echo "# Information [$SCRIPT_NAME]: Directory $PWD/venv already in existence... Overwrite? [y/n]"
        while :
        do
            read -n 1 k <&1 # User input read
            if [[ $k = y ]]
            then
                echo "# Information [$SCRIPT_NAME]: Deletion of directory $PWD/venv ..."
                echo "$ rm -r ./venv"
                rm -r ./venv
                if [ $? -ne 0 ]
                then
                    echo ""
                    echo "! Error [$SCRIPT_NAME]: Failed deletion of directory $PWD/venv"
                    exit 1
                fi
                echo "Done"
                echo ""
                break
            elif [[ $k = n ]]
            then
                echo ""
                echo "Aborted"
                exit 0
            else
                continue
            fi
        done
    fi
    echo "$ $BIN -m venv ./venv"
    $BIN -m venv ./venv
    if [ $? -ne 0 ]
    then
        echo "! Error [$SCRIPT_NAME]: Virtual environment set-up failure"
        exit 1
    fi
fi
echo "Done"
echo ""


# Virtual Environment Activation
echo "$ . venv/bin/activate"
. venv/bin/activate
if [ $? -ne 0 ]
then
    echo "! Error [$SCRIPT_NAME]: Activation failure for venv"
    exit 1
fi


# Executable File Location Check -- to be in Virtual Environment Directory
echo "# Information [$SCRIPT_NAME]: $BIN executable file-path check"
echo "$ which $BIN"
RESULT=$(which $BIN)
echo $RESULT
echo "Done"
echo ""


# iPyKernel Installation
echo "# Information [$SCRIPT_NAME]: Installation of ipykernel (via pip)..."
echo "$ $BIN -m pip install ipykernel"
$BIN -m pip install ipykernel
if [ $? -ne 0 ]
then
    echo "! Error [$SCRIPT_NAME]: Installation failure for ipykernel (via pip)"
    exit 1
fi
echo "Done"
echo ""


# Jupyter Installation
echo "# Information [$SCRIPT_NAME]: Installation of jupyter (via pip)..."
echo "$ $BIN -m pip install jupyter"
$BIN -m pip install jupyter
if [ $? -ne 0 ]
then
    echo "! Error [$SCRIPT_NAME]: Installation failure for jupyter (via pip)"
    exit 1
fi
echo "Done"
echo ""


# Attachment of Virtual Environment to iPyKernel
echo "# Information [$SCRIPT_NAME]: Installation of venv environment into ipykernel..."
echo "$ $BIN -m ipykernel install --prefix=$DIR/venv --name=$(basename $DIR)"
$BIN -m ipykernel install --prefix=$DIR/venv --name=$(basename $DIR)
if [ $? -ne 0 ]
then
    echo "! Error [$SCRIPT_NAME]: Installation failure for venv environment into ipykernel"
    exit 1
fi
echo "Done"
echo ""

exit 0




